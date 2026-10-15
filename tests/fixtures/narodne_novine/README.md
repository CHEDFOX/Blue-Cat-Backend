# Narodne novine fixtures

Three real act pages of Narodne novine, the official gazette of Croatia (https://narodne-novine.nn.hr), retrieved
2026-10-14 with the project's polite fetcher (identifying User-Agent, 5 s between requests), stored gzip-compressed and
byte for byte as received. They were fetched under the publisher's data access page (open, free, crawlers and
automated services welcome; https://narodne-novine.nn.hr/data_access.aspx) and članak 18. stavak 3. of the Copyright
Act (official texts published for the official information of the public are not protected); see
`config/sources/hr-narodne-novine.yaml`.

| file | act | what it exercises |
|---|---|---|
| `nn-2023-30-510.html.gz` | Zakon o službenoj iskaznici, NN 30/2023 (ELI `sluzbeni/2023/30/510`) | current markup with an ELI metadata block, article headings, paragraphs, points, a dash list, the closing text |
| `nn-2010-85-2422.html.gz` | Ustav Republike Hrvatske (pročišćeni tekst), NN 85/2010 (`sluzbeni/2010/85/2422`) | a consolidated text without ELI metadata, part headings in the context, articles of several lines |
| `nn-2005-42-816.html.gz` | Zakon o dopunama Zakona o boravišnoj pristojbi, NN 42/2005 (`sluzbeni/2005/42/816`) | Word-converted markup of the 2000s, an amending law quoting "Članak 16.a" inside an article |

The tests rebuild the connector's cache from these files and read them offline.
