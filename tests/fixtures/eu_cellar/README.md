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

Source: EUR-Lex / Cellar, Publications Office of the European Union. © European Union.
Reused under the Commission reuse notice (Commission Decision 2011/833/EU). These files are the
publisher's renditions of the Official Journal, not its authentic electronic edition.
