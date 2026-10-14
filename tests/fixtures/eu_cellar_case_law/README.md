# Cellar case-law test fixtures (real EU judgments)

Unmodified responses from the Publications Office of the European Union's Cellar
(`https://publications.europa.eu`), retrieved on 2026-10-14 by `CellarCaseLawConnector` with
`manifest.yaml`, stored in the connector's cache layout. Response bodies are gzip-compressed
(`*.gz`); each `*.meta.json` records the URL, status, content type and retrieval time.

| Judgment | CELEX | Court | Layout of the XHTML |
|---|---|---|---|
| Schrems, C-362/14 (EU:C:2015:650) | 62014CJ0362 | Court of Justice | converted from Formex |
| Digital Rights Ireland and Others, C-293/12 and C-594/12 (EU:C:2014:238) | 62012CJ0293 | Court of Justice | converted from Formex |
| Simmenthal, 106/77 (EU:C:1978:49) | 61977CJ0106 | Court of Justice | digitised Reports (capitals, numbers in the text) |
| Van Gend & Loos, 26/62 (EU:C:1963:1) | 61962CJ0026 | Court of Justice | digitised Reports (no point numbers) |
| Kadi v Council and Commission, T-315/01 (EU:T:2005:332) | 62001TJ0315 | General Court | digitised Reports |

English and French each. `cache/.../webapi/rdf/sparql/<celex>/` holds the two SPARQL answers per
judgment (work metadata; per-language designation, parties, title and files), `cache/.../resource/cellar/`
the XHTML files.

Source: Court of Justice of the European Union, via EUR-Lex / Cellar, Publications Office of the
European Union. © European Union. Reused under the Commission reuse notice (Commission Decision
2011/833/EU) and the Court's acknowledgement requirement; see `docs/jurisdictions/eu-case-law.md`.
These files are the publisher's renditions, not the Court's authentic versions.
