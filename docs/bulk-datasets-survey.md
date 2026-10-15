# Bulk datasets of law and court decisions: survey (2026-10-15)

Question: which law and case-law datasets published as datasets could fill the gaps in [corpus.md](corpus.md) ("Not loaded", 97
sources) and the countries that are only samples? Survey only: nothing was ingested, no parser written. Research aid, not legal advice;
no verdict below is counsel's. Descriptors for the ACCEPT rows are `config/sources/<id>.yaml` with `status: planned` and
`review_status: pending_review`.

**Result: 36 rows: 7 ACCEPT, 14 UNCLEAR, 15 REJECT** (a row may group near-identical re-hosts). Only seven datasets passed all four
tests; four are US federal material (one case-law source, statutes, regulations, the rest of the US Code) and three are new
court-decision sources (Brazil, Italy, Taiwan). Nothing usable was found for Spain, Japan, Finland, Sweden, Denmark, Ireland, Poland,
Estonia or Australia (federal): see "Gaps" below.

## Method

- Rules (from the brief): ACCEPT needs (a) the original publisher or a recognised steward, (b) the dataset's own licence words that permit
  automated use, storage and display, (c) the exact source text with its origin stated, (d) no restriction on the underlying content that
  the dataset ignores. A re-host of a restricted or blocked source is REJECT whatever licence the uploader chose; research-only is UNCLEAR.
- Discovery: Hugging Face `api/datasets` (search, card data, file tree, raw README; `datasets-server` statistics for one licence field),
  the AWS Open Data registry (1,219 dataset slugs scanned for law/court/legislation words: two legal datasets, both Indian), CKAN or
  Socrata catalogues of portals whose robots.txt allows the API path (data.gov.lv, data.gov.uk, opendata.swiss, datos.gov.co,
  open.canada.ca, data.gouv.fr), publishers' own download pages, and a few web searches (their snippets are marked "second-hand").
- Politeness: identifying agent `ULIS-research-bot/0.1`; robots.txt read per host and every path checked with `urllib.robotparser`; roughly
  250 requests in all, 10 s apart where a Crawl-delay existed; only metadata pages (HTML or JSON, up to 2.3 MB), bucket listings and 9 HEAD
  requests. No data file was downloaded; three PDFs of 0.2-1.7 MB were read for their terms.
- Refusals, each a stop on that host (no other agent, proxy or mirror): www.courtlistener.com robots.txt 403 (CloudFront); datos.gob.es API
  403; lovdata.no 405 (Varnish IPS); dagr.gov.lv 401; data.gov.ua download link 403 (Cloudflare); reyestr.court.gov.ua connection reset;
  elrc-share.eu certificate not verifiable; datos.gob.mx, legis.senado.leg.br, stf.jus.br, dati.gov.it 403; dati.normattiva.it 409.
- Not used because robots.txt disallows the path: the APIs of govdata.de, data.gov.ua, data.gov.au, data.gov.ie, data.europa.eu (search),
  data.overheid.nl, datos.gob.cl, datos.jus.gob.ar, dadosabertos.web.stj.jus.br (`/api/`), saos.org.pl (`/api/`); all of
  repository.overheid.nl, static.case.law and static.openlegaldata.io.
- **My lapse:** zenodo.org disallows `/api/records`; after the check printed "disallowed" a chained command still sent 3 queries to it. The
  answers were irrelevant (not law) and are not used; Zenodo was not searched further. Kaggle was not surveyed (its catalogue needs an
  account); a Kaggle copy of a publisher's data is a re-host and falls under the REJECT rule unless the publisher uploaded it. Internet
  Archive: only law.resource.org (below) was looked at.

## Table

| Dataset (URL) | Steward; scope; size and format | Licence (exact words) | Provenance | Verdict: reason |
|---|---|---|---|---|
| **CourtListener bulk data** https://wiki.free.law/c/courtlistener/help/api/bulk-data/bulk-legal-data (files: https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/list.html?prefix=bulk-data/) | Free Law Project; US federal and state opinions; 2026-09-30 snapshot: opinions 55.3 GB, clusters 2.5 GB, dockets 5.1 GB (bz2 CSV, quarterly) | "Our bulk data files are free of known copyright restrictions." (Public Domain Mark 1.0) | Court websites and other public sources incl. Harvard CAP (per the COLD and Common Pile cards); source code per cluster and download URL per opinion (to confirm) | **ACCEPT**: steward named in the brief, explicit statement. Condition: www.courtlistener.com refused the bot, so get FLP's written OK first |
| **GovInfo bulk: Statutes at Large, Public and Private Laws, Statute Compilations** https://www.govinfo.gov/bulkdata | GPO; US statutes as enacted; STATUTE folders for volumes 1-138, PLAW 113th-119th Congress, COMPS 2,705 XML files; USLM XML "beta" | "Copyright protection under this title is not available for any work of the United States Government" (17 U.S.C. 105, https://www.govinfo.gov/about/policies) | GPO's own files | **ACCEPT**: official publisher; same terms as active us-govinfo-* sources |
| **GovInfo bulk: CFR annual editions 1996-2026, Federal Register 2000-2026** same index | GPO; US regulations (point-in-time snapshots) and daily FR; XML | same | GPO's own files | **ACCEPT**: gives history the current-only eCFR source lacks |
| **US Code, all titles** https://uscode.house.gov/download.html | Office of the Law Revision Counsel; 53 per-title USLM zips (10 already used) or `xml_uscAll@119-120.zip` 108,675,360 bytes | No statement on the site (see us-uscode-house.yaml); GovInfo notice above for the same publication | OLRC | **ACCEPT** (extension of active source): same host, terms and parser |
| **STJ Portal de Dados Abertos: Íntegras de decisões e acórdãos** https://dadosabertos.web.stj.jus.br/dataset/integras-de-decisoes-terminativas-e-acordaos-do-diario-da-justica | Superior Tribunal de Justiça (Brazil); decisions and acórdãos from 2021-01-04, daily ZIP of texts + JSON + CSV dictionary (about 7 MB a day) | "Licença Creative Commons Atribuição" (version not stated) | The Court's own portal, texts of the Diário da Justiça | **ACCEPT**: court itself, CC BY, full texts; BR has no profile yet |
| **Corte costituzionale Open Data** https://dati.cortecostituzionale.it/Scarica_i_dati/Scarica_i_dati | Italian Constitutional Court; "circa 18.000" pronunce 1956-today (page also says 20,000), XML/CSV/JSON, zips of 15, 36, 57 MB, weekly | "I dati disponibili in vari formati sono rilasciati con licenza Creative Commons (CC BY SA 3.0) per il libero riuso." | The Court's archive | **ACCEPT** with caveat: ShareAlike may reach ULIS's database |
| **Judicial Yuan open data: judgments (裁判書)** https://opendata.judicial.gov.tw/ | 司法院, Taiwan; JSON by judgment id; size not verified | "以無償、非專屬，得再授權之方式提供公眾使用 ... 重製、改作、編輯、公開傳輸 ... 開發各種產品或服務 ... 使用時，應註明出處" (free, non-exclusive, sublicensable; reproduce, adapt, build products; name the source) | The Court administration itself | **ACCEPT** with caveat: operator-held account, token 6 h, service hours 00:00-06:00 |
| free-law/Caselaw_Access_Project (HF) https://huggingface.co/datasets/free-law/Caselaw_Access_Project | Free Law Project; US state and federal cases, parquet per state (61 files), "1M<n<10M" | card tag `cc0-1.0` only; README answers HTTP 401 (gated, manual) | Not readable | UNCLEAR: card unreadable; CAP's own host disallows all robots |
| harvard-lil/cold-cases (HF) https://huggingface.co/datasets/harvard-lil/cold-cases | Harvard LIL with FLP; "8.3 million United States legal decisions", 32.2 GB parquet | `license: cc0-1.0` | CourtListener bulk data, "reformats" it, "extraneous data removed" | UNCLEAR: text is processed, not shown to be verbatim; use the primary files |
| openlegaldata/court-decisions-germany (HF) https://huggingface.co/datasets/openlegaldata/court-decisions-germany | Open Legal Data e.V.; German decisions, "100K<n<1M", parquet dumps 2022-10-18 and 2026-05-20 | Imprint https://de.openlegaldata.io/pages/imprint/: "Für unsere Datenbank gilt die Open Database License (ODbL) v1.0. Amtliche Werke (Gesetze, Urteile, …) sind vom Urheberrecht ausgenommen (UrhG § 5)." | Card gated (401): "preprocessed"; per-decision source unseen | UNCLEAR: ODbL share-alike; if its federal-court decisions come from rechtsprechung-im-internet.de, that site disallows robots (unverified); the site's own robots.txt does point users to these dumps |
| voilaj/swiss-caselaw (HF) https://huggingface.co/datasets/voilaj/swiss-caselaw | Individual (J. Hertner); 1.05M records, about 909,000 unique decisions, 118 courts, daily, parquet | "dataset packaging and added metadata are dedicated under CC0-1.0 ... The underlying decision texts remain official published court decisions" | 59 scrapers of court portals; keeps "the version with the longest full text" | UNCLEAR: not a steward; terms of about 100 sites unverified |
| entscheidsuche.ch repository https://entscheidsuche.ch/pdf/EntscheidsucheAPI.pdf | Verein entscheidsuche.ch; Swiss federal and cantonal decisions as originally scraped HTML/PDF + JSON; scrapers listed per canton | "You are free to use the API and the cases in any way you want." (donation "not a legal requirement") | Scraped from courts; `Blockliste.json` for removals; source URL not confirmed | UNCLEAR: not the courts; their terms unverified (Art. 5 URG, cited in ch-fedlex.yaml for enactments, is said to cover decisions too: not verified here) |
| a2aj/canadian-case-law (HF) https://huggingface.co/datasets/a2aj/canadian-case-law | A2AJ (York U. and Toronto Metropolitan U.); about 227k cases, EN/FR, parquet | "Users must also comply with upstream licenses found in the `upstream_license` field ... These upstream licenses may include limits on commercial use"; first 123,643 rows say "See upstream license, including non-commercial use and other restrictions" (CHRT row: no restrictions found) | Court sites, "verbatim with minimal normalisation (e.g. HTML → plain text, whitespace cleanup)"; `url_en`/`url_fr` per row | UNCLEAR: university steward and good provenance, but most rows non-commercial |
| Latvia: Anonimizētie nolēmumi https://data.gov.lv/dati/dataset/anonimizetie-nolemumi | Tiesu administrācija (Courts Administration); decisions 2007-2026, metadata with ECLI + link to each file | CKAN `license_id` CC0-1.0 (https://creativecommons.org/publicdomain/zero/1.0/) | Court Administration; file format unknown | UNCLEAR: data host dagr.gov.lv answered 401 |
| Ukraine: ЄДРСР yearly archives https://data.gov.ua/en/dataset/af3b8bd7-4d50-4837-b3db-28545f43169d | State Judicial Administration; one zip per year, `documents.csv` with `doc_url` per decision (readme field list) | "Any person may freely copy, publish, disseminate, use, including for commercial purposes, public information in the form of open data ... with the mandatory reference to the source" | DSA order 1120 of 15.11.2019 | UNCLEAR: archive looks like metadata only; zip HEAD 403 (Cloudflare); texts on reyestr.court.gov.ua unreachable |
| UK Find Case Law https://caselaw.nationalarchives.gov.uk/permissions-and-licensing | The National Archives; courts and tribunals of England and Wales and UK-wide bodies; Atom feed, coverage not measured | Open Justice Licence v2.0 allows "commercial use, such as incorporating into products or applications"; but "You need additional permission (a separate licence) for computational analysis of Find Case Law judgments (including indexing by search engines)" | TNA | UNCLEAR: free licence application, caselawlicence@nationalarchives.gov.uk |
| Indian Supreme Court Judgments (AWS) https://registry.opendata.aws/indian-supreme-court-judgments/ | Dattam Labs (private lab), AWS-sponsored bucket; PDFs 1950-2025 + parquet metadata | "License CC-BY-4.0" (registry; the uploader's) | Scraped from scr.sci.gov.in; uploader's repo does not state the site's terms | UNCLEAR: licence is the re-uploader's, source terms not read |
| Argentina: Base SAIJ de Normativa Provincial https://datos.jus.gob.ar/dataset/base-saij-de-normativa-provincial | Ministerio de Justicia (SAIJ); CSV index of provincial norms from 1886 with a link to each text | "Licencia Creative Commons Attribution 4.0" | Ministry | UNCLEAR: index only; texts sit on saij.gob.ar (terms not found) |
| Slovakia: Ministry of Justice OpenAPI https://www.justice.gov.sk/sluzby/registre/otvorene-data/ | Ministerstvo spravodlivosti SR; court decisions API (Swagger) | No licence in the page or in the OpenAPI `info` | Ministry | UNCLEAR: ask servicedesk.mssr@justice.sk |
| Norway: Lovdata public data (api.lovdata.no) | Lovdata foundation; current acts and central regulations | Only second-hand: NLOD 2.0 (a GitHub wrapper, via search) | n/a | UNCLEAR: host answers 405 to the bot; not read |
| Not examined: MARCELL legislative subcorpora (ELRC-SHARE), AI-team-UoA/greek_legal_code (HF), FLITE Supreme Court decisions 1937-1975 (https://www.govinfo.gov/bulkdata/SCD) | various | not read | not read | UNCLEAR: ELRC-SHARE certificate error; Greek set is a labelled derivative; FLITE is one 52 MB zip of 2010 and duplicates us-govinfo-usreports |
| isaacus/open-australian-legal-corpus (HF) https://huggingface.co/datasets/isaacus/open-australian-legal-corpus | Isaacus (company); 232,560 texts, Commonwealth, NSW, QLD, WA, SA, TAS law and cases | Corpus: "Creative Commons Attribution 4.0"; High Court: "for personal, non-commercial use or use within an organisation"; Federal Court: "personal, non-commercial use"; NSW Caselaw: "excludes external robots from indexing decisions" | OCR (Tesseract), Inscriptis HTML-to-text | **REJECT**: not a steward; text is converted; contains non-commercial and no-robot material |
| Indian High Court Judgments (AWS) https://registry.opendata.aws/indian-high-court-judgments/ | Dattam Labs; about 17.8M judgments, 1.25 TiB | "License CC-BY-4.0" (uploader's) | Its scraper "Solves the eCourts arithmetic CAPTCHA automatically" | **REJECT**: obtained by defeating the portal's CAPTCHA |
| pile-of-law/pile-of-law (HF) | Researchers; US and other legal text, "10M<n<100M" | `license: cc-by-nc-sa-4.0` | Many sources | **REJECT**: non-commercial |
| joelniklaus/Multi_Legal_Pile (HF) | Researchers; multilingual, "10M<n<100M" | `license: cc-by-nc-sa-4.0` | Re-host of national corpora | **REJECT**: non-commercial re-host |
| CzCDC 1.0, LINDAT https://lindat.mff.cuni.cz/repository/xmlui/handle/11372/LRT-3052 | Charles University; 237,723 Czech top-court decisions, "unedited and obtained directly from the competent court" | "Creative Commons - Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)" | Courts | **REJECT**: non-commercial |
| common-pile/caselaw_access_project (HF) | Common Pile project; 6,919,240 documents from CAP and CourtListener | no licence field | "Erroneous OCR errors were further corrected ... additional post-processing" | **REJECT**: text modified; re-host |
| EUR-Lex re-hosts (joelniklaus/eurlex_resources, do-me/EUR-LEX, coastalcph/multi_eurlex ...) | Researchers | cc-by-4.0 / cc-by-sa-4.0 tags | Copies of EUR-Lex | **REJECT**: re-hosts of an already active source (eu-eur-lex) |
| rcds/* Swiss task datasets (HF) | Univ. of Bern RCDS group | cc-by-sa-4.0 (cc-by-nd for MultiLegalNeg) | Derived task sets | **REJECT**: derivatives, not the source text |
| Individual re-hosts: geniacllm/hanrei (JP), marianbasti/jurisprudencia-Argentina-SAIJ, vGassen/Dutch-* (NL), PiotrSty/saos-polish-court-judgments (PL), mrfg/turkish-court-decisions, lvanews/ukrainian-court-decisions, Manuel/sentencias-corte-cons-colombia, OpenDataMoroccanLaw/... (HF) | Individuals | cc0 / apache-2.0 / wtfpl / unknown | Scraped from sites whose robots or terms block bulk use (SAOS `/api/`, Rechtspraak "Het is niet mogelijk een complete set te downloaden") | **REJECT**: re-hosts of restricted or rate-limited sources |
| lbox/lbox_open (HF) | LBox, Korea | `cc-by-nc-4.0` | Korean precedents | **REJECT**: non-commercial |
| Wikisource via https://dumps.wikimedia.org/legal.html | Wikimedia; community transcriptions | "Except as discussed below, all original textual content is licensed under the GNU Free Documentation License (GFDL)" (the rest of the sentence was not read) | Volunteers | **REJECT**: community transcriptions, not shown to be the official text; copyleft |
| Colombia: Sentencias proferidas por la Corte Constitucional https://www.datos.gov.co/Justicia-y-Derecho/Sentencias-proferidas-por-la-Corte-Constitucional/v2k4-2t8s | Corte Constitucional; 1992-2026 | "Creative Commons Attribution \| Share Alike 4.0 International" | Court | **REJECT**: metadata only (Proceso, Sentencia, Fecha, ponente), no text |
| Chile: BCN linked open data https://datos.bcn.cl/ | Biblioteca del Congreso; norms dataset | no licence line found | BCN | **REJECT**: metadata and links, no text (Ley Chile text is CC BY-NC-ND, see cl-bcn-leychile) |
| Zurich Baurekursgericht on opendata.swiss | Canton Zürich | opendata.swiss terms "by" | RSS feed | **REJECT**: RSS summaries, not bulk text |
| Netherlands: Rechtspraak open data https://www.rechtspraak.nl/uitspraken/open-data | Rechtspraak | "Het is niet mogelijk een complete set te downloaden" | Publisher | **REJECT** as a dump: none exists; nl-rechtspraak stays a per-ECLI sample |

## ACCEPT, ranked by value (value = documents x gap filled)

1. **CourtListener bulk data** (`us-courtlistener-bulk-s3`). Millions of US opinions (COLD, built from it, says 8.3 million); ULIS holds only
   the Supreme Court's bound volumes and the appeals courts' PDFs (`us-govinfo-usreports`, `us-govinfo-uscourts`). Next step: owner asks FLP in writing (the website refused the bot), then downloads the latest
   `courts`, `dockets`, `opinion-clusters`, `opinions` files (bz2 CSV, PostgreSQL COPY; stream, never load) on the server. New connector
   modelled on `dila_dump.py` and parser modelled on `rechtspraak.py`/`eu_cellar_case_law.py`, registered in `ulis/ingestion/pipeline.py`;
   citations in `ulis/jurisdictions/modules/us.py`. Caveats: read `schema-<date>.sql` first; drop records CourtListener blocks or deindexes
   (COLD says it filters them) if the files carry a flag; take the columns that hold the court's own text, not any with publisher-added citation
   mark-up; some texts are OCR (COLD's data dictionary has an `ocr` field).
2. **US Code, all titles** (extend `us-uscode-house`, no new descriptor). 43 of 53 title archives are missing; the existing connector
   downloads `xml_usc<NN>@119-120.zip`. Next step: add the titles to `config/manifests/us-uscode-house.yaml` with their sha256 (the
   alternative single file is 108.7 MB). Caveat: titles not enacted into positive law (26, 42 ...) are prima facie evidence only (1 U.S.C. 204);
   the parser already sets `positive_law=false`, so the owner decides whether to show them.
3. **GovInfo statutes** (`us-govinfo-bulk-statutes`). Enacted laws 2013-today and Statutes at Large volumes plus 2,705 compilations: the "as
   enacted" layer under the Code. Next step: inspect two volumes of `/bulkdata/STATUTE` and one `PLAW/119/public` listing, then reuse
   `ulis/sources/parsers/uslm.py` with a manifest connector like `govinfo.py`. Caveat: "beta" XML; early volumes may be absent; compilations are
   editorial.
4. **Judicial Yuan** (`tw-judicial-yuan-opendata`). Fills Taiwanese case law, the most permissive court-data licence found. Next step: the owner
   registers an account, stores user/password as secrets, and a connector calls Auth, JList (changes of the day 7 days back) and JDoc between
   00:00 and 06:00 Taiwan time; ask the Judicial Yuan how to obtain the back catalogue. Parser: JSON text into the `tw` module. Caveat: attachments
   and PDFs; personal-data law; Chinese terms read by me, not by a lawyer.
5. **STJ Brazil** (`br-stj-dados-abertos`). First Brazilian decisions (daily since 2021). Next step: a connector that reads the dataset page
   (no `/api/`, Crawl-delay 10) for the resource links, downloads `<date>.zip` + `metadados<date>.json`; parser pairs each text with its metadata;
   a new `br` profile and module (none exists). Caveat: CC version unstated; dates are milliseconds since 1970 in the JSON.
6. **GovInfo CFR annual editions and Federal Register** (`us-govinfo-bulk-cfr-fr`). Next step: manifest of year folders; reuse the `ecfr.py`
   parser for CFR XML and `ulis/ingestion/snapshots.py` (`revision_snapshot`) so each edition is one dated version; FR as dated rule documents.
7. **Corte costituzionale** (`it-corte-costituzionale-opendata`). About 18-20k pronunce, three zips of 108 MB in all. Next step: read the DTD
   (https://dati.cortecostituzionale.it/Scarica_i_dati/I_dtd/I_dtd), write a parser into the `it` module's judgments. Caveat: CC BY-SA 3.0.

## UNCLEAR and REJECT: what would change the verdict

- **Ask in writing (add to permission-requests.md):** Free Law Project (confirm bot download from the bucket); The National Archives (free
  computational-analysis licence, also unlocks gb-find-case-law); Latvian Courts Administration (access to the DAGR file link);
  Slovak Ministry of Justice (licence of the API); the State Judicial Administration of Ukraine (text download); Open Legal Data e.V.
  (card and per-decision source of the gated dump; ODbL share-alike); entscheidsuche.ch (provenance per court, removal handling); A2AJ
  (a list of rows whose upstream licence allows commercial use); Lovdata (public data terms, host blocks the bot).
- **Never:** re-hosts whose uploader changed the licence (Pile of Law, MultiLegalPile, Common Pile, Australian corpus), anything obtained by
  defeating a CAPTCHA (Indian High Courts), non-commercial sets (CzCDC, LBox). If the owner later accepts non-commercial use for a closed
  test only, ULIS's licence gate (`commercial_use`) is the place to enforce it, not a change of these verdicts.
- **Probably copies of active sources (names only, not opened):** a2aj/canadian-laws, rcds/swiss_legislation, voilaj/swiss-legislation and
  harvard-lil/cold-french-law; the publishers' own active sources are the better route. Also https://law.resource.org/pub/us/case/ (Public.Resource.Org
  court opinions, directories last modified 2014-2017, robots.txt allows it, no licence page read): probably overlaps CourtListener (not checked).

## Gaps in corpus.md and the countries named in the brief

| Gap | Result |
|---|---|
| US case law | CourtListener bulk (ACCEPT, conditional); CAP via Hugging Face is gated and CAP's own host disallows robots |
| US Code, statutes, regulations | US Code all titles, GovInfo statutes, CFR/FR (ACCEPT) |
| Brazil, Italy, Taiwan courts | STJ, Corte costituzionale, Judicial Yuan (ACCEPT) |
| Switzerland | no steward dataset: Swiss RCDS sets are derivatives; entscheidsuche.ch and voilaj/swiss-caselaw UNCLEAR; opendata.swiss only an RSS feed |
| Germany | Open Legal Data UNCLEAR (gated, ODbL); BVerfG and federal courts remain blocked by robots |
| UK | Find Case Law needs the free computational licence; legislation already loaded |
| Canada | A2AJ UNCLEAR (mostly non-commercial upstream terms); open.canada.ca has only one courts-martial CSV |
| Australia (federal) | no dataset; the Open Australian Legal Corpus is REJECT; data.gov.au API disallowed |
| EU | only third-party copies of EUR-Lex; Cellar (active) is the source; data.europa.eu search API disallowed |
| Spain, Japan, Finland, Sweden, Denmark, Ireland, Poland, Estonia, Netherlands | no usable dataset: datos.gob.es API 403; courts.go.jp offers no dataset and no terms page was found; Finlex has no court decisions (court-decisions-survey.md); SAOS `/api/` disallowed; Estonia: a search snippet (ERR) reports a Ministry proposal to stop court rulings as open data; Rechtspraak says a complete download is not possible |
| Latvia, Ukraine, Slovakia, Argentina | UNCLEAR rows above (CC0 / statutory reuse terms but access or text not confirmed) |
| Lithuania, Romania, Chile, Mexico, Korea, Singapore, India Code, Kenya, South Africa | not established: portals refused (403) or only second-hand search snippets; existing descriptors unchanged |
