# Cellar test fixtures (real EU law)

Unmodified responses from the Publications Office of the European Union's Cellar
(`https://publications.europa.eu`), retrieved on 2026-10-14 by `CellarConnector` with
`manifest.yaml`, stored in the connector's cache layout. Response bodies are gzip-compressed
(`*.gz`); each `*.meta.json` records the URL, status, content type and retrieval time.

| Work | CELEX | Files |
|---|---|---|
| Treaty on European Union, consolidated version 2016 (OJ C 202, 7.6.2016) | 12016M/TXT | XHTML, en/fr/de |
| Charter of Fundamental Rights of the European Union (OJ C 202, 7.6.2016) | 12016P/TXT | Formex 4, en/fr/de |
| Regulation (EC) No 593/2008 (Rome I) | 32008R0593 | Formex 4, en/fr/de |
| Directive 2011/83/EU (consumer rights) | 32011L0083 | Formex 4, en/fr/de |

`webapi/rdf/sparql/<celex>/` holds the three SPARQL answers per work (metadata, changes, files).

## Batched manifest and consolidated version (added 2026-10-15)

`manifest-batched.yaml` (`sparql_batch: 20`) lists the consolidated version of the GDPR in English, French and German
(CELEX 02016R0679-20160504, base act 32016R0679) and Rome I in English. `webapi/rdf/sparql/batch/` holds the three
batched answers (metadata of both works and of the base act, changes of Rome I, files of both), unmodified, retrieved on
2026-10-15. The Rome I files are the ones above. The consolidated GDPR files
(`resource/cellar/5f2552c2-cc45-11e6-ad7c-01aa75ed71a1.0001.01` German, `.0021.01` French, `.0022.01` English; `DOC_1`/`DOC_6` the
CONS.ACT, the other the Official Journal descriptor) are the full Formex 4 files with the recitals and all articles but
1, 6, 37 and 41 removed (the act envelope, `INFO.CONSLEG`, title, kept articles and final formula are byte for byte as
received; article 37(1)(c) and 41(3) are the two that the consolidated text corrects).

Source: EUR-Lex / Cellar, Publications Office of the European Union. © European Union.
Reused under the Commission reuse notice (Commission Decision 2011/833/EU). These files are the
publisher's renditions of the Official Journal, not its authentic electronic edition.

## Acts held without a Formex 4 file (added 2026-10-18)

`manifest-text.yaml` (`sparql_batch: 20`, English) lists two acts in force that Cellar holds only in a text format
other than Formex 4: Directive 2002/59/EC (CELEX 32002L0059, vessel traffic monitoring) as the **legacy EUR-Lex HTML**
(`resource/cellar/9150683a-2d91-414a-9c7f-e206ede0fa20.0004.01/DOC_1`, `text/html`, 59 KB) and Council Decision
2006/245/EC (CELEX 32006D0245, Protocol to the EC-Switzerland agreement on the free movement of persons) as the **OJ
XHTML** of the CONVEX converter (`resource/cellar/554b1fbc-1f09-468f-8728-eeed87d5b536.0005.02/DOC_1`,
`application/xhtml+xml`, 225 KB, with the Protocol, its annexes and declarations). `webapi/rdf/sparql/batch/` holds the
four batched answers (metadata and changes of both works, the HTML files of the first, the XHTML files of the second),
unmodified, retrieved on 2026-10-18 together with the two files.
