#!/usr/bin/env python
# -*- encoding: utf-8 -*-

import click
import json
import logging
from collections import defaultdict, OrderedDict
from sys import exit
from strephit.commons.io import load_corpus, load_scraped_items
from strephit.commons import parallel
from numpy import average
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

logger = logging.getLogger(__name__)

VERBAL_PREFIXES = {
    'en': 'V'
}


def get_similarity_scores(verb_token, vectorizer, tf_idf_matrix):
    """ Compute the cosine similarity score of a given verb token against the input corpus TF/IDF matrix.
        :param str verb_token: Surface form of a verb, e.g., *born*
        :return: cosine similarity score
        :rtype: ndarray
    """
    verb_token_vector = vectorizer.transform([verb_token])
    # Here the linear kernel is the same as the cosine similarity, but faster
    # cf. http://scikit-learn.org/stable/modules/metrics.html#cosine-similarity
    scores = linear_kernel(verb_token_vector, tf_idf_matrix)
    logger.debug("Corpus-wide TF/IDF scores for '%s': %s" % (verb_token, scores))
    logger.debug("Average TF/IDF score for '%s': %f" % (verb_token, average(scores)))
    return scores


def produce_lemma_tokens(pos_tagged_path, pos_tag_key, language):
    """ Extracts a map from lemma to all its tokens
        :param pos_tagged_path: path of the pos-tagged corpus
        :param pos_tag_key: where the pos tag data is in each item
        :param language: language of the corpus
        :return: mapping from lemma to tokens
        :rtype: dict
    """
    corpus = load_scraped_items(pos_tagged_path)
    lemma_tokens = defaultdict(set)

    for item in corpus:
        for token, pos, lemma in item.get(pos_tag_key, []):
            if pos.startswith(VERBAL_PREFIXES[language]):
                lemma_tokens[lemma.lower()].add(token.lower())

    return lemma_tokens


def compute_tf_idf_matrix(corpus_path, document_key):
    """ Computes the TF-IDF matrix of the corpus
        :param corpus_path: path of the corpus
        :param document_key: where the textual content is in the corpus
        :return: a vectorizer and the computed matrix
        :rtype: tuple
    """
    corpus = load_corpus(corpus_path, document_key, text_only=True)
    vectorizer = TfidfVectorizer()
    return vectorizer, vectorizer.fit_transform(corpus)


class TFIDFRanking:
    def __init__(self, vectorizer, verbs, tfidf_matrix):
        self.vectorizer = vectorizer
        self.verbs = verbs
        self.tfidf_matrix = tfidf_matrix

    def score_lemma(self, lemma):
        tf_idfs, st_devs = [], []
        for token in self.verbs[lemma]:
            scores = get_similarity_scores(token, self.vectorizer, self.tfidf_matrix)
            tf_idfs += filter(None, scores.flatten().tolist())
            st_devs.append(scores.std())

        return lemma, average(tf_idfs), average(st_devs)

    def find_ranking(self, processes=0):
        tfidf_ranking = {}
        stdev_ranking = {}
        for lemma, tfidf, stdev in parallel.map(self.score_lemma, self.verbs, processes):
            tfidf_ranking[lemma] = tfidf
            stdev_ranking[lemma] = stdev
        return (OrderedDict(sorted(tfidf_ranking.items(), key=lambda x: x[1], reverse=True)),
                OrderedDict(sorted(stdev_ranking.items(), key=lambda x: x[1], reverse=True)))


class PopularityRanking:
    def __init__(self, corpus_path, document_key, verbs):
        self.corpus = load_corpus(corpus_path, document_key, text_only=True)
        self.verbs = verbs

    @staticmethod
    def _bulkenize(iterable, bulk_size):
        acc = []
        for each in iterable:
            acc.append(each)
            if len(acc) % bulk_size == 0:
                yield acc
                acc = []

        if acc:
            yield acc

    def score_from_text(self, documents):
        scores = defaultdict(int)
        for each in documents:
            text = each.lower()
            for lemma, tokens in self.verbs.iteritems():
                scores[lemma] += sum(text.count(t) for t in tokens)
        return scores

    def find_ranking(self, processes=0, bulk_size=100, normalize=True):
        ranking = defaultdict(int)
        for score in parallel.map(self.score_from_text,
                                  self._bulkenize(self.corpus, bulk_size),
                                  processes):

            for k, v in score.iteritems():
                ranking[k] += v

        ranking = OrderedDict(sorted(ranking.items(), key=lambda x: x[1], reverse=True))

        if normalize:
            max_score = float(ranking[next(iter(ranking))])
            for lemma, score in ranking.iteritems():
                ranking[lemma] = score / max_score

        return ranking


def harmonic_ranking(*rankings):
    product = lambda x, y: x * y
    sum = lambda x, y: x + y
    get = lambda k: (r[k] for r in rankings)

    return OrderedDict(sorted(
        [(k, len(rankings) * reduce(product, get(k)) / (1 + reduce(sum, get(k)))) for k in rankings[0]],
        key=lambda (_, v): v,
        reverse=True
    ))


@click.command()
@click.argument('pos_tagged', type=click.Path(exists=True, dir_okay=False))
@click.argument('document_key')
@click.argument('language')
@click.option('--pos-tag-key', default='pos_tag')
@click.option('--dump-verbs', type=click.File('w'), default='dev/verbs.json')
@click.option('--dump-tf-idf', type=click.File('w'), default='dev/tf_idf_ranking.json')
@click.option('--dump-stdev', type=click.File('w'), default='dev/stdev_ranking.json')
@click.option('--dump-popularity', type=click.File('w'), default='dev/popularity_ranking.json')
@click.option('--dump-final', type=click.File('w'), default='dev/verb_ranking.json')
@click.option('--processes', '-p', default=0)
def main(pos_tagged, document_key, pos_tag_key, language, dump_verbs, dump_tf_idf, dump_stdev, dump_popularity,
          dump_final, processes):
    lemma_tokens, (vectorizer, tf_idf_matrix) = parallel.execute(
        2,
        produce_lemma_tokens, (pos_tagged, pos_tag_key, language),
        compute_tf_idf_matrix, (pos_tagged, document_key)
    )

    pop_ranking = PopularityRanking(pos_tagged, document_key, lemma_tokens).find_ranking(processes)
    tfidf_ranking, stdev_ranking = TFIDFRanking(vectorizer, lemma_tokens, tf_idf_matrix).find_ranking(processes)

    final_ranking = harmonic_ranking(pop_ranking, tfidf_ranking, stdev_ranking)

    json.dump(tfidf_ranking, dump_tf_idf, indent=2)
    json.dump(stdev_ranking, dump_stdev, indent=2)
    json.dump(pop_ranking, dump_popularity, indent=2)
    json.dump(lemma_tokens, dump_verbs, default=lambda x: list(x), indent=2)
    json.dump(final_ranking, dump_final, indent=2)


if __name__ == '__main__':
    exit(main())
