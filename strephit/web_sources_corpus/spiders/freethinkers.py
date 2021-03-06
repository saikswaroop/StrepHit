# -*- coding: utf-8 -*-
import json
import re

import scrapy

from strephit.web_sources_corpus.items import WebSourcesCorpusItem
from strephit.commons import text


class FreethinkersSpider(scrapy.Spider):
    name = "freethinkers"
    allowed_domains = ["en.wikisource.org"]
    start_urls = (
        'https://en.wikisource.org/wiki/A_Biographical_Dictionary_of_Ancient,_Medieval,_and_Modern_Freethinkers',
    )

    def parse(self, response):
        current_item = None

        for p in response.xpath('.//div[@id="mw-content-text"]/p'):
            content = p.xpath('child::node()')
            if content and content[0].xpath('local-name()').extract() == ['a']:
                if current_item is not None:
                    if 'other' in current_item:
                        current_item['other'] = json.dumps(current_item['other'])
                    yield current_item

                current_item = WebSourcesCorpusItem(
                    url=text.clean_extract(content[0], '@href'),
                    name=text.clean_extract(content[0], 'text()'),
                    bio=' '.join(text.clean_extract(c, './/text()') for c in content[1:])
                )
            else:
                txt = p.xpath('text()').extract()[0]
                m = re.match(ur'([^(]{,50})\((about )?(B\.C\. )?(\d+| ) ?- ?(\d+| )\)', txt)
                if m:
                    if 'other' in current_item:
                        current_item['other'] = json.dumps(current_item['other'])
                    yield current_item
                    current_item = WebSourcesCorpusItem(
                        url=response.url,
                        name=m.group(1).strip(),
                        birth=(m.group(3) or '') + m.group(4),
                        death=(m.group(3) or '') + m.group(5),
                        bio=text.clean_extract(p, './/text()'),
                    )
                elif current_item is not None:
                    current_item['bio'] += text.clean_extract(p, './/text()')

        if current_item is not None:
            if 'other' in current_item:
                current_item['other'] = json.dumps(current_item['other'])
            yield current_item
