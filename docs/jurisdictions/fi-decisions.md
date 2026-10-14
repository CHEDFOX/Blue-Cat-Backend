# Finland (FI): decisions of the Data Protection Ombudsman from Finlex open data

Status: **draft unverified profile (0.2.0), whole package ingested.** About 400 decisions of the Data Protection Ombudsman
(tietosuojavaltuutettu), Finnish and Swedish, linked to the Acts in [fi.md](fi.md). **These are not court decisions:** the Ombudsman
is a supervisory authority (profile entry `FI-TSV`, kind `administrative_body`). Finlex's open data has **no court decisions**; see the
findings below. Research aid, not legal advice.

| | |
|---|---|
| Source / manifest | `config/sources/fi-finlex-dpo-decisions.yaml` (active, approved), `config/manifests/fi-finlex-dpo-decisions.yaml` |
| Connector / parser | `ulis/sources/connectors/finlex_decisions.py`, `ulis/sources/parsers/finlex_decisions.py` (`finlex_akn_decision` 1.0.0) |
| Profile / citations | `config/jurisdictions/fi.yaml` (0.2.0: `FI-TSV`), `ulis/jurisdictions/modules/fi.py` (1.1.0) |
| Tests | `tests/test_finlex_decisions.py` (14), fixtures in `tests/fixtures/finlex_decisions/` (5 real decisions) |

## Source and licence (read 2026-10-14, robots.txt first)

| Page | Finding |
|---|---|
| `www.finlex.fi/robots.txt` | `User-Agent: *` / `Disallow: /_next/image`: the download path is allowed. |
| [FAQ](https://www.finlex.fi/en/faq) | "There are no restrictions on use ... of the data published in Finlex." "Finlex open data is available under ... CC BY 4.0. The data may not be used for purposes that breach or compromise the protection of personal data ... may not be combined in a manner that violates a person's privacy." |
| [Download materials](https://www.finlex.fi/en/open-data/download-materials) | Daily zip packages per document type; "Decisions of Data Protection Ombudsman (3.6 MB)", also Chancellor of Justice (227 MB) and Parliamentary Ombudsman (567 MB). No court decisions. |
| Copyright Act 404/1961, 9 § 1 mom. 4 kohta | "Tekijänoikeutta ei ole: ... 4) viranomaisen tai muun julkisen toimielimen päätöksiin ja lausumiin." |

Conclusion: licence **approved** (CC BY 4.0, attribution on every result; counsel review recommended, in particular the personal-data
conditions). One request for the package (`PoliteHttp`, identified, cached), one for robots.txt.

## Why not the Supreme Court (KKO)

The Finlex API knows three `judgment` types (chancellor-of-justice, data-protection-ombudsman, parliamentary-ombudsman decisions;
`.../judgment/supreme-court-precedent/list` and `.../kko/list` answer 404). KKO/KHO decisions are only on the Finlex website, rendered by
script, and on the Court's own site, which reserves "all rights" beyond the Copyright Act ("tekstiä voi lainata, mutta tällöin lähde
on mainittava"). Recorded as `fi-kko-precedents` (planned); a confirmation from the Court or the Legal Register Centre is needed.
The Chancellor of Justice and Parliamentary Ombudsman decisions keep their text in attached PDFs and are not ingested.

## What is ingested

`data-protection-ombudsman-decision.zip`: 396 Akoma Ntoso `judgment` documents (382 Finnish, 14 Swedish expressions) of 385
decisions, 1999-2026 (11 decisions exist in both languages and share one work). Each `main.xml` is stored byte for byte.

## How a decision is read

- **Fields**: diary number (`dnro`) as docket, `dateIssued` as decision date, `dateProduced` as version date, `docTitle` as case name
  and as the `headnote` paragraph; `ext` holds the Finlex legal-basis class (e.g. "EU:n yleisen tietosuoja-asetuksen mukainen
  päätös"), the keywords and the Finlex identifier. Languages `fi`/`sv`, not authoritative.
- **Paragraphs** (exact text, whitespace collapsed, `<br/>` as a line break, footnote marks as `[n]`): `heading`, `text`, `item`,
  `introduction` (notes on later history, e.g. an appeal decision); no paragraph numbers exist in the source.
- **Links**: a statute named with its Statute Book number in brackets, "(1050/2018)", is linked (extraction method `pattern`) to
  `/fi/act/{year}/{number}`; nothing else is linked.

## Citations and authority

`Tietosuojavaltuutettu, päätös 8.8.2019, dnro 28/523/2018` / `Dataombudsmannen, beslut 26.7.2005, dnr 1164/41/2005`. Every hit is
`persuasive_precedent` ("Persuasive only"): the Ombudsman's decisions do not bind; a later court judgment on the same decision is
noted in the text's introduction at best.

## Real run and tests (dev DB `ulis_courts`)

`migrate`, `sync-catalog`, `ingest fi-finlex-dpo-decisions`: 396 documents, 385 judgments, 23,446 paragraphs and passages, 1,581
statute links, no errors (1 min). With the seven Acts of `fi-finlex` ingested from the local cache, 730 links resolve (Tietosuojalaki
1050/2018: 478; Hallintolaki 434/2003: 246; others not ingested). `verify-sources`: 935 pass, 0 fail in the dev DB (396 of them these decisions). Searches (`FI`): "tietoturvaloukkaus ilmoitus" -> decision 23.3.2022, dnro 2437/161/22; "henkilötunnus kuluttajalasku" ->
decision 14.5.2020, dnro 8205/154/18; "personuppgifter insyn rätt" -> the Dataskyddslag 38 § 3 mom. and decision 20.7.2022 (dnr 1601/452/18).
Tests: 14 in `tests/test_finlex_decisions.py`; full suite 780 passed (434 s); ruff check, ruff format --check, mypy clean.

## Known gaps

- Authority decisions only, about 400; no KKO/KHO or other court decisions, no Chancellor of Justice or Parliamentary Ombudsman decisions.
- Parts of decisions are redacted by the Ombudsman. One decision (2025/2469) is in English inside a Finnish expression.
- Links are at work level; the GDPR and other EU instruments are not linked (not ingested); later history is only what the text states.
