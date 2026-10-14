# Sweden (SE): case law reports from Domstolsverket's open data

Status: **draft unverified profile (0.2.0), full manifest, sandbox slice ingested.** The official case reports
(rättsfallsreferat) of the Swedish higher courts, Swedish only, linked to the Acts in [se.md](se.md). Research aid, not
legal advice.

| | |
|---|---|
| Source / manifest | `config/sources/se-rattspraxis.yaml` (active, approved), `config/manifests/se-rattspraxis.yaml` (20 courts, all reports) |
| Connector / parser | `ulis/sources/connectors/rattspraxis_se.py`, `ulis/sources/parsers/rattspraxis_se.py` (`rattspraxis_se_json` 1.0.0) |
| Profile / citations | `config/jurisdictions/se.yaml` (0.2.0: courts `SE-AD`, `SE-MIOD`, `SE-MMOD`, `SE-PMOD`, `SE-MD`), `ulis/jurisdictions/modules/se.py` |
| Tests | `tests/test_rattspraxis_se.py` (16), fixtures in `tests/fixtures/rattspraxis/` (7 real reports) |

## Source and licence (read 2026-10-14, robots.txt first)

| Page | Finding |
|---|---|
| `rattspraxis.etjanst.domstol.se/robots.txt` | 404: none, so no restrictions. `www.domstol.se/robots.txt`: `Allow: /`. |
| [Sök rättspraxis, frågor och svar](https://www.domstol.se/tjanster-och-blanketter/sok-rattspraxis/) | "Finns rättspraxis som öppna data? Ja, de rättsfallsreferat, domar och beslut från de högsta instanserna ... finns som öppna data." Referat from 1981, judgments and decisions from March 2025. |
| [Öppna data](https://www.domstol.se/om-webbplatsen-och-digitala-kanaler/oppna-data/) | Links the technical documentation: the OpenAPI file [`/openapi/puh-openapi.yaml`](https://rattspraxis.etjanst.domstol.se/openapi/puh-openapi.yaml) ("PUH API", no key). |
| [Sveriges dataportal, dataset 601_3755](https://www.dataportal.se/datasets/601_3755) | Metadata (Turtle from `admin.dataportal.se/store/601/metadata/3755`): publisher Domstolsverket, `dcterms:license` **CC0 1.0**, distribution `/api/v1/publiceringar` (JSON). |

Conclusion: licence **approved** (CC0 per the publisher's dataset record; the court pages only say "öppna data"; counsel review
recommended). Retrieval: `PoliteHttp`, identified User-Agent, sequential, 5 s apart, pages of 50 reports cached under
`var/sources/`. The reports carry no party names (the publisher pseudonymises).

## What is ingested

`GET /api/v1/publiceringar?domstolkod=<code>&publiceringsformer=REFERAT&asc=true&pagesize=50&page=n` for each court; one
source document per report (its JSON object, canonical). Committed manifest: all **16,505** reports of 2026-10-14 (HDO 5,262;
HFD 1,152; REGR 1,727; ADO 2,028; MIOD 533; MMOD 729; MOD 709; PMOD 203; MDO 478; hovrätter 3,575; kammarrätter 109), about
330 requests, roughly 30 minutes. **Sandbox run (temporary manifest, newest 25 per court):** HDO, HFD, ADO, MIOD, MMOD,
PMOD, HSV, KST: 200 reports (2011-2026), 200 judgments, 17,530 paragraphs (1,230 numbered), 227 statute links.
The judgments and decisions themselves (`DOM_ELLER_BESLUT`, PDF attachments) and leave-to-appeal notices are not ingested.

## How a report is read

- **Fields**: court code to ULIS court (`HDO` to `SE-HD`, `HFD`/`REGR` to `SE-HFD`, hovrätter to `SE-HOVR`, kammarrätter to
  `SE-KR`, special courts to their own); the actual court is kept in `ext.court_name`. Decision date `avgorandedatum`, case numbers
  as docket, version date = `publiceringstid`. Citation = the report number (`NJA 2026 s. 347`, `HFD 2026 ref. 35`, `MIG 2026:9`);
  case name = the nickname (`"Bryggan på Sippsö"`) or the report number. `ext` holds `typ`, all report numbers, keywords, legal
  areas, statutes, cited cases (free text), literature, preparatory works.
- **Paragraphs** (exact text, whitespace collapsed, soft hyphens removed): `headnote` (sammanfattning), `heading` (h1-h4), `text`,
  `item`, and `considerations`: the court's numbered reasons, read as the longest, latest run of at least five consecutive numbers
  1, 2, 3 ... that fills three quarters of the text between them; `num` holds the number, removed from the text. Lower courts'
  sections of a report stay unnumbered; reports without such a run (old reports, hovrätt, AD) have no numbers.
- **Links**: the publisher's `lagrumLista` SFS numbers become `source_markup` links from the headnote to `/se/sfs/{year}/{n}`
  (work level); resolved only for the eight ingested statutes.

## Citations and authority

`NJA 2026 s. 347 p. 6` (report number, then `p.` and the paragraph); without a report number: `Svea hovrätt 2026-04-17, mål nr
Ö 1309-26`. Sweden has no formal stare decisis (profile: `jurisprudence_constante`): every hit is `persuasive_precedent` ("Persuasive
only"); the rationale quotes the profile's note that prejudikat are followed in practice (to verify).

## Real run and tests (dev DB `ulis_courts`)

See the counts above; `ingest se-riksdagen-sfs` (8 statutes, from the local cache) then the slice: no errors; of 227 links, 34 resolved
(Rättegångsbalken 1942:740: 14, Brottsbalken 1962:700: 8; the other referenced statutes are not ingested). Searches (`SE`): "skadestånd
kränkning skälig tid" -> NJA 2026 s. 347; "uppsägning arbetsbrist turordning" -> 22 § lagen (1982:80) om anställningsskydd;
"uppehållstillstånd anknytning" -> MIG 2025:10 / MIG 2026:2. `verify-sources`: 935 pass, 0 fail in the dev DB (200 of them
reports). Tests: 16 in `tests/test_rattspraxis_se.py`; full suite 780 passed (434 s); ruff check, ruff format --check, mypy clean.
The API ignores `asc` unless `sortorder` is given (it then lists newest first), so the connector always sends
`sortorder=avgorandedatum`.

## Known gaps

- Reports only (about 16,500), not the courts' own judgments and decisions; tingsrätter and förvaltningsrätter absent, hovrätter 3,575 and kammarrätter 109 reports in all; no citator (cited cases stay free text in `ext`), statute links at work level only.
- Numbers are read from the text (an enumeration of five or more items would be taken for reasons); report text is not the authentic NJA.
- Paging is by date with no total; a list changing during the run can repeat a report (reported) or skip one.
