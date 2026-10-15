# Licence and provenance review of the active sources

Independent review, 2026-10-15, of the 51 sources with `status: active`. Not legal advice. It tells the owner which licence bases are enough for the closed early-access test, which are not enough for a commercial offering, and what to do next.

## Method

- **Sources read.** I listed the sources with `grep -l "^status: active" config/sources/*.yaml` (51) and read every YAML in full.
- **Other files read, only where a point was unclear.** Notes: `docs/jurisdictions/{li,sk,tw,nl}.md`. The Polish manifest header. `docs/temporal-model.md` and `docs/sources-and-licensing.md`. The code that serves results or sets dates: `ulis/retrieval/{details,lookup,search}.py`, `ulis/api/app.py`, `ulis/verification/limitations.py`, `ulis/temporal/validity.py`, `ulis/ingestion/snapshots.py`, `ulis/sources/registry.py`. The connectors `fedlex.py`, `slovlex_static.py` and `subnational_http.py`, and the `validity_basis` setting of every parser.
- **Evidence.** No network access. Every fact about a publisher below is what the repo records, mostly quotes in the YAML headers. I did not re-check any of it. Where the repo does not settle a point, I say "not determinable from the repo".
- **GREEN:** the publisher's own words, or a statute together with an affirmative invitation from the publisher, cover automated retrieval, storage, display and commercial reuse. Access followed robots.txt and the terms, and the dates are honest. Only routine counsel confirmation remains.
- **AMBER:** one concrete gap. Either commercial use is not covered or only inferred, the terms are unread or in conflict, there is a conduct question, or there is an attribution or date defect that config or code can fix. Usable in the closed test.
- **RED:** a machine-readable signal from the publisher runs against what ULIS does with the text, and no permission is on file.
- **What "approved" means.** `review_status: approved` in every YAML is the ULIS project's own sign-off (`reviewed_by: ULIS project ...`), not counsel's. No class below is a legal opinion.

## Summary

| Source | Class | One-line reason |
|---|---|---|
| `sk-slovlex-static` | RED | Every page ULIS fetches carries `noindex, nofollow`; ULIS indexes the pages and follows the history tables; terms unread |
| `ar-infoleg` | AMBER | CC BY rests on general portal terms; the CC BY footer of the norm pages is commented out |
| `au-federal-register` | AMBER | Licence clear; the attribution sentence returned has a placeholder, not the download date |
| `au-qld-legislation` | AMBER | Same: the returned text reads "at [full date of download]" |
| `au-tas-legislation` | AMBER | Same: the returned text reads "at [full date of download]" |
| `ca-mb-manitoba-laws` | AMBER | That OpenMB covers statutes is ULIS's own reading of "most government information" |
| `ca-on-e-laws` | AMBER | Licence clear; bot manager in front; the connector keeps going after a challenge page (stops at 3) |
| `ch-fedlex` | AMBER | File URLs from a host whose robots.txt disallows `/filestore/*` are rewritten to the website host |
| `ch-fedlex-treaties` | AMBER | Same fetch path; Art. 5 URG coverage of treaties "to verify"; stands in for blocked or restricted depositaries |
| `cz-esbirka-opendata` | AMBER | Statutory basis only; no publisher terms readable; database rights not examined |
| `dk-retsinformation` | AMBER | Licence explicit (commercial); dates: earlier consolidations run on past interim amendments |
| `ee-riigi-teataja` | AMBER | Statutory basis only; open-data terms page unreadable; database rights not examined |
| `eu-cellar-case-law` | AMBER | Decision 2011/833 does not cover Court documents of its own force; the Court's own notice was not re-read |
| `fr-dila-cass` | AMBER | Licence Ouverte; a personal-data "Avertissement" the data sheet names was never found |
| `fr-dila-constit` | AMBER | As CASS, and its attribution text wrongly states that the decisions are pseudonymised |
| `fr-dila-inca` | AMBER | As CASS; stock not read in full |
| `fr-dila-jade` | AMBER | As CASS |
| `gb-uksc-judgments` | AMBER | OGL for Crown copyright "(to verify)"; TNA licenses computational analysis of judgments separately |
| `hr-narodne-novine` | AMBER | Statute plus invitation to crawlers, but "all content protected by copyright"; as-published acts labelled `in_force` |
| `ie-irish-statute-book` | AMBER | Licence explicit; as-enacted text resolves as `in_force` today, against the YAML's own condition |
| `it-normattiva` | AMBER | `commercial_use: unknown`; must say the text is "gratuito"; one-off snapshots dated as starts |
| `li-lilex` | AMBER | No terms and no robots.txt; the YAML's "tell info.rdr@llv.li before the full manifest" is not enforced |
| `lu-legilux` | AMBER | CC BY 4.0 versus general terms "personal use"; files fetched from the website host, not the data host |
| `nl-bwb` | AMBER | CC0, but KOOP's hosts block named AI crawlers, including a "rag-kennisbank"; ULIS has an LLM assistant |
| `pl-eli-sejm` | AMBER | Statutory basis; dates: the older tekst jednolity runs on past amendments that took effect in between |
| `si-uradni-list` | AMBER | Publisher's exemption is "not a licence text"; as-published acts labelled `in_force` |
| `tw-moj-laws` | AMBER | Licence explicit, but terms pages under `Disallow: /` and a 5xx robots host were requested; attribution is a licence condition |
| `us-ecfr` | AMBER | ecfr.gov blocked the IP (403); the same compilation was then taken from GPO bulk data |
| `us-or-ors` | AMBER | No licence or terms at all; `commercial_use: unknown` |
| `us-tx-statutes` | AMBER | Statute reserves state copyright; terms may sit in a robots-disallowed /Help/ area; future-effective texts attested as current |
| `us-ut-code` | AMBER | Terms grant copying "for personal use"; commercial use only by implication ("without proper attribution") |
| `us-wa-rcw` | AMBER | Statute Law Committee "claims copyright for both codes"; would-be sellers must contact it |
| `ca-justice-laws-xml` | GREEN | Reproduction of Federal Law Order plus OGL-Canada |
| `de-gesetze-im-internet` | GREEN | "zur freien Nutzung und Weiterverwendung" plus § 5 UrhG |
| `es-boe-consolidada` | GREEN | AEBOE reuse conditions: "fines comerciales y no comerciales" |
| `eu-eur-lex` | GREEN | Publications Office gives Cellar the Commission reuse notice; the EUR-Lex legal notice is still to be read in a browser |
| `fi-finlex` | GREEN | CC BY 4.0, "no restrictions on use" |
| `fi-finlex-dpo-decisions` | GREEN | CC BY 4.0; personal-data condition to observe |
| `fr-dila-legi` | GREEN | Licence Ouverte 2.0 with the DILA's attribution list |
| `gb-legislation-gov-uk` | GREEN | OGL v3.0 plus the fair-use policy |
| `jp-egov-laws` | GREEN | PDL 1.0, commercial use included |
| `lv-likumi` | GREEN | Statute excludes "oficiālās konsolidētās versijas"; terms forbid nothing relevant |
| `nl-rechtspraak` | GREEN | "U mag informatie ... overnemen, als u de bron vermeldt" plus art. 11 Auteurswet; 30-decision sample |
| `se-rattspraxis` | GREEN | CC0 1.0 in the publisher's dataset record |
| `se-riksdagen-sfs` | GREEN | "fria att användas och spridas vidare så länge du anger källa" |
| `ulis-fictional-sample` | GREEN | Self-authored, CC0 |
| `us-govinfo-uscourts` | GREEN | 17 U.S.C. 105; GovInfo invites sitemap harvesting |
| `us-govinfo-usreports` | GREEN | 17 U.S.C. 105; GovInfo invites sitemap harvesting |
| `us-gpo-uslm-samples` | GREEN | CC0 waiver in LICENSE.md and in `dc:rights` |
| `us-uscode-house` | GREEN | 17 U.S.C. 105; robots.txt restricts only Slurp |
| `uy-impo` | GREEN | Licencia de Datos Abiertos: "uso comercial y no comercial" |

Counts: **1 RED, 31 AMBER, 19 GREEN.**

## Cross-cutting findings: fix before testers use the API directly

- **C1. Attribution is not returned with two kinds of result.**
  - **Where it is returned.** `/v1/search` hits carry the source's `attribution` (`_source_dict(asdict(SourceInfo))`, `ulis/retrieval/search.py:549`, filled from `licence.attribution_text` at `details.py:97`). So do the research assistant's results (`assistant/tools.py:353`, `schemas.py:208`).
  - **Where it is missing.** `/v1/provisions` (`provision_at`, `lookup.py:79-87`) returns only `source_id, source_name, document_id, sha256, retrieved_at, locator, licence_id`: no attribution and no licence name. `/v1/judgments` (`judgment_detail`) returns every paragraph's text with no source block at all. A client could join the result with `/v1/sources`, but the result itself does not carry the attribution.
  - **What breaks.** These promises "on every result" are not met on those two endpoints: the Art. 49 PublO notice (`ch-*`); the `tw-moj-laws` attribution without which "the licence is deemed never granted (OGDL s 3(2))"; the disclaimers that licence 3.2 says to retain (`ie-irish-statute-book`); "carattere non autentico e gratuito" (`it-normattiva`); "© King's Printer for Ontario" (`ca-on-e-laws`); and every CC BY, OGL and Licence Ouverte notice.
- **C2. "approved" overstates the review.** `docs/sources-and-licensing.md` (lines 3-5) says licence statements "are recorded with `review_status: pending_review` and must be confirmed by counsel". Yet all 51 active sources say `approved`, signed off by the project. A publisher or court reading `approved` would assume a legal sign-off.
- **C3. The gate ignores commercial use.** `licence_gate` (`registry.py:88`) checks the status, the review status, `automated_processing`, `bulk_download` and that a connector exists. It never checks `commercial_use` or `redistribution`. Four active sources have `commercial_use: unknown`: `us-wa-rcw`, `us-tx-statutes`, `us-or-ors` and `it-normattiva`. Nothing would stop a paid deployment from serving them.
- **C4. Inconsistent standard for US states.** `us-or-ors` and `us-tx-statutes` are active on "no terms found". `docs/corpus.md` keeps `us-mn-statutes` ("Robots allow it, but no reuse terms were located") and `us-fl-statutes` planned on the same finding. One rule should apply to all four.
- **C5. Challenge handling.** The shared connector for QLD, TAS, ON and MB (`subnational_http.py:21-23`) says: "a bot-challenge page ... is recorded in `fetch_errors` and the run goes on, but three unrecognisable documents in a row stop the run" (`MAX_CONSECUTIVE_UNRECOGNISED = 3`). The project's own rule is to stop on any refusal. This matters for `ca-on-e-laws`, which sits behind a bot manager.
- **C6. Temporal documentation gap.** The basis table in `docs/temporal-model.md` lists `source_periods`, `enactment_date_presumed`, `attested_at_edition` and `proposal_stage`. It omits `revision_snapshot`, which 17 parsers set. The document a reviewer checks does not describe the most common basis.

## RED

**`sk-slovlex-static`**

- **Signal against the use.** The YAML (lines 11-13) says the pages "carry `<meta name="robots" content="noindex, nofollow">` (an instruction to search engines about indexing; robots.txt does not disallow them)". ULIS is a search index: it stores and serves the text of exactly those pages. Its connector "reads the act's history page ... and then fetches the version pages" (`slovlex_static.py:1-7`), deriving them from that page's table. That is what `nofollow` asks robots not to do.
- **Weak basis.** The terms "could not be read (JavaScript application)", and the approval is "on the statutory basis only". § 21f of 211/2000 Z. z. grants a public licence only for information published "na účel opakovaného použitia". The repo does not show that the static pages are such a publication.
- **Signal the other way.** www.slov-lex.sk/robots.txt allows everything and lists sitemaps on the static host. That invites crawling, not indexing.
- **No other problem.** The static host has no robots.txt (404); applying the www rules to it is extra caution, not a defect. Dates are consistent with the YAML: `revision_snapshot` with the source's `data-from` and `data-to`.
- **Resolve by:** set `status: suspended` until the Ministerstvo spravodlivosti SR confirms in writing that ULIS may retrieve and index these pages, or until a person reads the portal's terms in a browser and they permit it.

## AMBER

### Fetch path or terms in question

- **`ch-fedlex`.**
  - *Robots:* "fedlex.data.admin.ch/robots.txt disallows /filestore/* for all user agents". ULIS takes the file URLs from that host's SPARQL endpoint, and `public_url()` (`connectors/fedlex.py:46-50`) rewrites them to `www.fedlex.admin.ch/filestore/`, whose robots.txt allows everything except print views. RFC 9309 applies rules per host, so the letter of robots.txt is kept. But the files' canonical host closes that path, and the rule "never work around a block" needs the publisher's answer.
  - *Licence (strong):* "Die Konsultation sowie das Herunterladen von Daten zu Verwertungszwecken ist unentgeltlich"; "Das XML Format ist für eine maschinelle Verarbeitung der Daten angedacht".
  - *Commercial:* Art. 49(1)(h) PublO allows access "for a fee only in a value-added (refined) form". The Art. 49(1)(c) notice is missing on `/v1/provisions` (C1).
  - *Resolve:* written confirmation from the Federal Chancellery; no new fetch runs until then.
- **`ch-fedlex-treaties`.** Same fetch path and conditions as `ch-fedlex`, plus:
  - "To verify (not read in this session): that Art. 5 URG names international treaties among the unprotected official texts".
  - It is used "because the treaty offices and courts that publish these texts themselves (Council of Europe, UN, ICC, ICJ) are blocked or restrict reuse". That is lawful substitution, not a work-around, but counsel should confirm it before commercial use.
- **`lu-legilux`.**
  - *Licence:* CC BY 4.0, "qui autorise leur réutilisation pour des utilisations commerciales ou non". It is stated on the help page, on data.public.lu and in every file's `license` element.
  - *Conflict:* the general conditions allow documents, "sauf indication contraire", for "personal, private information only, unmodified and not distributed outside the site" (the YAML's summary); "the two statements are not reconciled by the publisher".
  - *Robots:* data.legilux.public.lu "disallows /eli/* and /filestore/*", but legilux.public.lu "allows /filestore/eli/etat/leg/". That is an explicit allow on the serving host, better support than Fedlex has.
  - *Resolve:* one confirmation from the Service central de législation.
- **`tw-moj-laws`.**
  - *Ingestion:* compliant; it stayed on the allowed `/LawClass/LawAll.aspx`.
  - *Terms review:* "The licence and API pages are themselves under robots.txt 'Disallow: /'; each was read once, by hand". Yet `docs/jurisdictions/tw.md:170-172` counts them among the build's "16 HTTP requests to law.moj.gov.tw (robots.txt twice, home page, open-data declaration, API docs (2 incl. redirect) and spec, one law page by hand ...)". The data.gov.tw licence page was fetched after "robots.txt answered 500", and RFC 9309 says to assume complete disallow on a 5xx. Whether a person or the build agent made these requests is not determinable from the repo.
  - *Licence:* explicit, including commercial use, but OGDL s 3(2) voids it without the annex attribution (C1).
  - *Resolve:* fix C1; a person reads disallowed terms pages in a browser.

### Copyright claims, "personal use", commercial use unknown

- **`us-wa-rcw`.**
  - *Claim:* anyone intending to sell the RCW "is advised to contact the Washington State Statute Law Committee, which claims copyright for both codes".
  - *Basis:* `commercial_use: unknown`; "Approved for research use ... on that reading only" (the YAML cites Georgia v. Public.Resource.Org).
  - *Before commercial use:* the Committee's written answer and counsel on the government-edicts doctrine.
- **`us-tx-statutes`.**
  - *Reservation:* Gov. Code 323.0145(d)(2): access "does not alter, diminish, or relinquish any copyright ... of the State of Texas".
  - *Terms:* "its Help pages, where terms might be, are therefore not read" (capitol.texas.gov disallows /Help/). `commercial_use: unknown`.
  - *Dates:* "Text of section effective until ..." notes "stay in the text and are not turned into dates". A future-effective variant is therefore attested as current (`attested_at_edition`).
- **`us-or-ors`.** "no copyright statement, no terms about automated use"; `commercial_use: unknown`. This is the finding that keeps `us-mn-statutes` planned (C4). *Before commercial use:* written terms from the Legislative Counsel Committee.
- **`us-ut-code`.**
  - *Terms:* "anyone may view, copy, or distribute information ... for personal use ... A person or entity may not use any part of the information ... for commercial purposes ... without proper attribution of source. The Legislature reserves the right ... to elect to charge a fee".
  - *Gap:* ULIS's use is neither personal nor yet commercial. Commercial use is allowed only by implication, and the attribution it requires is missing on two endpoints (C1).
- **`it-normattiva`.**
  - *Licence:* reproduction "è consentita purchè venga menzionata la fonte, il carattere non autentico e gratuito". `commercial_use: unknown`. "La raccolta ... è frutto di una selezione redazionale" (IPZS S.p.A.). The open-data host answered 409 and was correctly left alone. A paid service that must say the text is free needs IPZS's view.
  - *Dates:* five acts are one export "as it applies on" 2026-10-14, recorded as `revision_snapshot` from that day with `currency_date=None` (`parsers/normattiva.py:17-20, 760-766`). The Codice civile therefore resolves as `in_force` "from 2026-10-14" on any later date. `attested_at_edition` with currency 2026-10-14 would match `de-gesetze-im-internet`.
- **`nl-bwb`.**
  - *Licence:* CC0 is unconditional, and the repository host has no robots.txt.
  - *Publisher's preference:* wetten.overheid.nl "disallows named AI/LLM crawlers (GPTBot, ClaudeBot, anthropic-ai, CCBot, "rag-kennisbank", ...) entirely"; overheid.nl and data.overheid.nl "also disallow ClaudeBot and other AI crawlers". ULIS's assistant is an LLM tool loop over the corpus (`docs/research-assistant.md`; `assistant/llm.py` uses the `anthropic` SDK). The YAML itself says to review this "before ULIS is used for ... retrieval-augmented generation at scale".
  - *Resolve:* ask KOOP.

### Statute-only bases, terms unreadable or silent

- **`cz-esbirka-opendata`.**
  - *Basis:* open-data host ("ESEL OpenData", no robots.txt, CORS `*`), plus 121/2000 § 3 and 106/1999 § 5a.
  - *Gap:* "No licence or terms text appears in the listing or the files"; portal terms unreadable; "Database rights, if any, are not examined".
- **`ee-riigi-teataja`.**
  - *Basis:* the "avaandmed" archive, plus Copyright Act § 5(1) p 3 and RTS §§ 7(1) and 12(1).
  - *Gap:* "No licence or terms text of the publisher's own was ... available"; database rights not examined.
  - *Conduct:* careful (about 25 requests, robots.txt applied to every act page). RTS § 12(1) requires a note and link, which depends on C1.
- **`hr-narodne-novine`.**
  - *Licence, for:* "Access is provided to any internet crawler who seeks to index and use the content", and članak 18(3) ZAPSP removes copyright from official texts.
  - *Licence, against:* "Svi sadržaji ... zaštićeni su autorskim pravom i mogu se koristiti samo pod uvjetima propisanim Zakonom". Whether Narodne novine d.d. claims database rights "is not stated".
  - *Dates:* the parser sets no `validity_basis` (default `source_periods`) and no dates. So 4,769 as-published laws since 1990, repealed ones included, carry version status `in_force` (`in_force_date_unverified`). The warning text is honest; the label is not.
- **`si-uradni-list`.**
  - *Licence:* "Omejitve uporabe gradiv ne veljajo za Uradni list Republike Slovenije", but "The exemption is a statement by the publisher, not a licence text; whether it covers automated copying is not spelled out". The Slovenian copyright statute is not quoted in the repo.
  - *Dates:* the same `in_force` label issue as Croatia.
- **`li-lilex`.**
  - *Basis:* Art. 5 URG ("Gesetze, Verordnungen ... und andere amtliche Erlasse" and "amtliche ... Sammlungen" unprotected); no terms, no robots.txt; "No explicit permission for automated reuse exists".
  - *Unenforced condition:* the YAML's "tell info.rdr@llv.li before running the full manifest (about 2,500 requests)" is not enforced. Only a slice has run (`li.md:23,45`), but `load-all` would run all 1,227 laws.
- **`pl-eli-sejm`.**
  - *Licence:* Prawo autorskie art. 4 plus the Chancellery's public API.
  - *Dates:* each tekst jednolity is dated from its "stan prawny" with `valid_to=None` (`eli_sejm.py:526,570,613`), and the snapshot rules continue its wording to the next text. The earlier of two texts (for example Kodeks karny DU/2022/1138, then DU/2024/17) therefore shows as in force until the later text's date, even where an amending act took effect in between.
  - *Disclosure:* only amendments after the newest text become outstanding changes; `SNAPSHOT_DATES` warns about the start, not the end; the YAML does not disclose this.

### Dates, attribution, conduct and case law (licence otherwise explicit)

- **`dk-retsinformation`.**
  - *Licence:* "ubegrænset brugsret ... bruges kommercielt og ikke-kommercielt", the cleanest in the set.
  - *Dates:* the same end-date mechanics as Poland (`retsinformation.py:326`) for the earlier lovbekendtgørelser of Forvaltningsloven and Funktionærloven. The YAML does say amending acts are "lifecycle events, not merged into the text".
- **`ie-irish-statute-book`.**
  - *Condition:* "Do not present the as-enacted text as the law currently in force".
  - *Dates:* `enactment_date_presumed` gives each provision `valid_from` = enactment and no end. So `resolve_at` returns `in_force` for 2026, and `lookup.py:61` sets `applies_on_date` true, even for an amended Act such as the Non-Fatal Offences Act 1997. The warnings exist, but the status contradicts the condition.
- **`gb-uksc-judgments`.**
  - *Licence:* the terms grant OGL for "Crown copyright material from this website"; that judgments are Crown copyright is "(to verify)".
  - *Computational analysis:* the YAML avoids Find Case Law because "computational analysis needs a licence", yet ULIS's LLM assistant analyses the same judgments from another host. Ask TNA or the Court.
- **`eu-cellar-case-law`.** "a judgment is the Court's document, so the Decision covers it only through the Publications Office's licence statement", and the Court's copyright page "was NOT re-read for this source".
- **`fr-dila-cass`, `fr-dila-inca`, `fr-dila-jade`.**
  - *Licence:* Licence Ouverte 2.0 is taken from DILA data sheets dated 2017; the Etalab text was not fetched.
  - *Personal data:* the reuser answers for the "loi Informatique et Libertés, conformément au document Avertissement", which "was not found on the DILA site". The assistant sends these decisions to an LLM provider.
  - *INCA:* "the stock was not downloaded for this source in the build environment".
- **`fr-dila-constit`.** The header says decisions "are published with the names the Council gives". Yet the `attribution_text` says "les données nominatives ... sont pseudonymisées par l'éditeur". The conditions and limitations repeat "Pseudonymised" and mention "the Cour de cassation's list of texts applied". These lines were copied from CASS, and the attribution text is false for this source.
- **`au-federal-register`, `au-qld-legislation`, `au-tas-legislation`.** CC BY 4.0, commercial use included. The publishers prescribe "... at [full date of download]", but the stored text keeps the placeholder. QLD and TAS return "at [full date of download]" literally; the federal source returns "[date of download: see the source document's retrieval date]".
- **`ca-on-e-laws`.**
  - *Licence:* "permits any person to reproduce ... without seeking permission and without charge".
  - *Conduct:* the pages are behind "a bot manager (a script from validate.perfdrive.com)", and the connector stops only "after three pages in a row that are not statutes" (C5).
  - *Notice:* the year in "© King's Printer for Ontario, 2012-23" is still to be confirmed.
- **`ca-mb-manitoba-laws`.** "that statutes are covered is ULIS's reading and should be confirmed with the King's Printer for Manitoba". If that reading is wrong, the source has no basis at all.
- **`ar-infoleg`.** The terms "licencia todos sus contenidos bajo ... CC BY 4.0 ... excepto cuando se declare lo contrario", but "The CC BY line in the footer of the norm pages is commented out in the HTML". Whether that was deliberate is not determinable from the repo.
- **`us-ecfr`.**
  - *Conduct:* ecfr.gov answered "Your IP address has been blocked ... Support Code 02" ("Not retried and not worked around"), and its robots.txt closes the full-text API. The same compilation was then taken from GPO's bulk-data repository.
  - *Why it is not worse:* that repository is offered separately, robots.txt allows it, and "OFR and GPO do not restrict downstream uses".
  - *Still to do:* tell OFR, so the project knows whether the block was aimed at ULIS.

## GREEN

- **`ca-justice-laws-xml`:** the Reproduction of Federal Law Order allows reproduction "without permission, with due diligence, if not represented as an official version", plus OGL-Canada. Pinned commit.
- **`de-gesetze-im-internet`:** "Die Rechtsnormen in deutscher Sprache stehen in allen angebotenen Formaten zur freien Nutzung und Weiterverwendung zur Verfügung". robots.txt allows all; translations (consent needed) are excluded.
  - Date check: `attested_at_edition` with `currency_date` = build date (`gesetze_im_internet.py:547,554`), so dates resolve to `in_force_date_unverified` with `ATTESTED_ONLY_AT`. Matches the temporal model and the YAML.
- **`es-boe-consolidada`:** "permiten la reutilización ... para fines comerciales y no comerciales". "Basado en datos de la Agencia Estatal Boletín Oficial del Estado" is in the attribution.
- **`eu-eur-lex`:** the Cellar distributions carry the "European Commission reuse notice" (Decision 2011/833 art 4: commercial reuse "without individual application"). EUR-Lex itself was not used (WAF challenge). Its legal notice should still be read by a person.
- **`fi-finlex`, `fi-finlex-dpo-decisions`:** "There are no restrictions on use ... Finlex open data is available under a Creative Commons Attribution 4.0 (CC BY 4.0) licence". The personal-data condition binds the Ombudsman decisions.
- **`fr-dila-legi`:** "Les données sont réutilisables gratuitement sous licence ouverte v2.0". Paternity, URL, file name and date are in the attribution.
  - Date check: `source_periods` from DATE_DEBUT and DATE_FIN, with the 2999 open end and the 2200+ placeholders handled (`dila_legi.py:23,55`). Consistent.
- **`gb-legislation-gov-uk`:** "All content is available under the Open Government Licence v3.0 except where otherwise stated". The fair-use limits are kept.
- **`jp-egov-laws`:** PDL 1.0 s 1 permits use "including commercially". The processing statement that s 1.1 requires is in the attribution.
- **`lv-likumi`:** Autortiesību likums 6(1) excludes legislative acts and their "oficiālās konsolidētās versijas". The terms forbid only acts against the portal's "drošību, stabilitāti un ātrdarbību". Disallowed earlier editions are not fetched. Database rights become a question if the scope grows.
- **`nl-rechtspraak`:** "U mag informatie van deze website overnemen, als u de bron vermeldt", plus art. 11 Auteurswet. Pseudonymised text. Commercial use is inferred, but from two independent bases.
- **`se-rattspraxis`:** the publisher's dataset record gives `dcterms:license` CC0 1.0. **`se-riksdagen-sfs`:** "Riksdagens öppna data är fria att användas och spridas vidare så länge du anger källa".
- **`us-govinfo-uscourts`, `us-govinfo-usreports`:** 17 U.S.C. 105, and "Sitemaps can be used by systems to crawl and harvest content from GovInfo". The key-only API is avoided.
- **`us-gpo-uslm-samples`:** CC0 in LICENSE.md. **`us-uscode-house`:** 17 U.S.C. 105; robots.txt restricts only Slurp.
- **`uy-impo`:** "para uso comercial y no comercial podrán ser: enajenados, reproducidos, distribuidos ...". Crawl-Delay 10 is kept.
- **`ulis-fictional-sample`:** self-authored CC0, marked fictional on every result.
- **Date checks for two AMBER sources, both consistent with their YAMLs:** eCFR uses `attested_at_edition` with currency = the file's Last-Modified (`ecfr.py:618-623`); CZ uses `revision_snapshot` with the source's own effective-from and effective-to dates.

## Actions for the owner, in priority order

1. **Code, before testers call `/v1/provisions` or `/v1/judgments`:** add `attribution` and `licence_name` to the `source` block in `provision_at` and give `judgment_detail` a `source` block (C1); render the real download date in the AU attribution sentences; correct `fr-dila-constit`'s attribution, conditions and limitations.
2. **Suspend `sk-slovlex-static`** and ask the Slovak Ministry of Justice whether ULIS may retrieve and index the static pages despite `noindex, nofollow`.
3. **Permission requests.** Use the template in `docs/permission-requests.md` and add these rows:
   - (a) Swiss Federal Chancellery: the `/filestore` mapping, Art. 49(1)(h) for a paid service, and whether Art. 5 URG covers treaties. Make no new Fedlex runs until it answers.
   - (b) Washington Statute Law Committee, Texas Legislative Council, Oregon Legislative Counsel and Utah OLRGC: use in a research service and commercial use.
   - (c) IPZS: commercial use and the "gratuito" wording.
   - (d) KOOP: AI- or RAG-assisted use of the CC0 BWB.
   - (e) Liechtenstein Rechtsdienst (info.rdr@llv.li): the notice the YAML requires, before any full run.
   - (f) Luxembourg Service central de législation: CC BY versus the conditions d'utilisation.
   - (g) TNA and the UK Supreme Court: computational analysis of judgments.
   - (h) OFR (eCFR support, code 02): the IP block.
   - (i) King's Printer for Manitoba: whether OpenMB covers statutes. King's Printer for Ontario: the year in the notice.
   - (j) Argentina Ministry of Justice: CC BY for InfoLEG texts.
   - (k) Court of Justice of the EU and the Publications Office (op-copyright@): reuse of case law.
4. **A person, in a browser:** read the terms ULIS could not read (e-Sbírka, Riigi Teataja open data, Slov-lex, capitol.texas.gov /Help/, the EUR-Lex legal notice, the Court of Justice's copyright page, the Etalab Licence Ouverte 2.0, the DILA "Avertissement"), and record wording and date in each `licence` block.
5. **Counsel questions:** sui generis database rights for the statute-only bases (CZ, EE, HR, LI, LV, PL, SI, SK, IT); the government-edicts doctrine for state codes (WA, TX, OR, UT); GDPR, and the French rules on reuse of identity data in published decisions, for case law sent to an external LLM provider (FR, NL, SE, FI-DPO, EU, UKSC); "value-added" under Art. 49(1)(h) PublO; Utah's "personal use" grant; relying on one State's publication for multilateral treaties.
6. **Config and code changes I recommend but did not make:**
   - (a) record counsel review separately from the project's `approved` (for example `counsel_review: pending`), and align `docs/sources-and-licensing.md` (C2). Its register also lists `eu-eur-lex` both as active and as planned.
   - (b) a commercial gate (`commercial_use` in allowed or allowed_with_conditions) for any paid deployment (C3);
   - (c) stop the subnational connector on the first challenge page (C5);
   - (d) a `notified_on`-style guard on the full `li-lilex` manifest;
   - (e) dates:
     - Normattiva's single exports: `attested_at_edition` with a currency date;
     - PL and DK: end a snapshot's wording at the first later amending act's commencement, or mark the end unattested;
     - IE, HR and SI: do not resolve as-enacted or as-published texts to `in_force`;
     - TX: date the "effective on/until" variants;
   - (f) document `revision_snapshot` in `docs/temporal-model.md` (C6);
   - (g) one standard for US states with no terms (C4).
