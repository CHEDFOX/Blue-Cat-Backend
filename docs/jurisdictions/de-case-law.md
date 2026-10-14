# Germany (DE): federal case law from Rechtsprechung im Internet - NOT INGESTED

Status: **planned, blocked by robots.txt.** No German decision is in ULIS. The source is recorded
in `config/sources/de-rechtsprechung-im-internet.yaml` (`status: planned`); the licence gate
refuses it. German federal law (Gesetze im Internet) is unaffected, see [de.md](de.md).

## What was checked (2026-10-14)

| URL | Finding |
|---|---|
| https://www.rechtsprechung-im-internet.de/robots.txt | Redirects to `/jportal/docs/eclicrawler/robots.txt`: `User-agent: *` / `Disallow: /` / `User-agent: DG_JUSTICE_CRAWLER` plus 252 daily ECLI `Sitemap:` lines. Every path (`/rii-toc.xml`, the decision zips, the Hinweise and Impressum pages) is disallowed for the ULIS bot; only the European e-Justice Portal's ECLI crawler is let in (checked with `urllib.robotparser`). |
| https://www.gesetze-im-internet.de/urhg/__5.html | § 5 Abs. 1 UrhG: "Entscheidungen und amtlich verfaßte Leitsätze zu Entscheidungen genießen keinen urheberrechtlichen Schutz." |
| [e-Justice Portal, Nationale Rechtsprechung - Deutschland](https://e-justice.europa.eu/topics/legislation-and-case-law/national-case-law/de_de) (updated 2025-04-11; seen only as a search-engine excerpt, the page itself did not resolve) | Selected decisions of the BVerfG, the supreme federal courts and the BPatG from 2010, updated daily; "Die Entscheidungen sind anonymisiert und werden grundsätzlich ungekürzt veröffentlicht"; "Die Entscheidungen stehen in allen angebotenen Formaten zur freien Nutzung und Weiterverwendung zur Verfügung"; the courts' own websites offer decisions "zur nicht gewerblichen Nutzung". |
| https://testphase.rechtsinformationen.bund.de/robots.txt, https://docs.rechtsinformationen.bund.de/robots.txt | Alternative (federal legal information portal, test phase): `User-agent: *` / `Disallow: /`. Not usable either. |

Conclusion: the legal basis is sound (official works, reported free re-use), but the publisher's
access policy forbids automated retrieval by anyone except the ECLI crawler. Under the onboarding
rules ULIS respects robots.txt and never works around a refusal (no other user agent, no mirror,
no browser automation), so **nothing beyond the robots.txt files was fetched**: 4 HTTP requests
in total (rii robots.txt twice - one automatic retry - and the two portal robots.txt files). The
toc, the decision zips and the site's own Hinweise/Impressum were not requested, so whether the
texts and headnotes are "amtlich" is unverified. Copies of the robots.txt files are kept in
`tests/fixtures/rechtsprechung_im_internet/`.

To proceed, one of: written permission from the publisher (Bundesamt für Justiz) for the
identified research bot, or a robots.txt that admits it, or a federal portal/API whose robots.txt
and terms permit machine access. The courts' own sites (non-commercial use only, as reported) and
third-party collections of unclear licence are not suitable.

## What is ready for a permitted source

- **Courts.** The DE profile already has BVerfG (rank 1), BGH, BVerwG, BAG, BFH, BSG (rank 2, no
  appeal links). BPatG would have to be added (appeals to the BGH). Senates are not courts.
- **Norm references.** Statute provisions have stable URIs (`/de/bund/bgb/~para-823-abs-1`,
  `/de/bund/gg/~art-1-abs-1`) and `resolve_uri` maps Gesetze im Internet URLs to them, so
  references such as "§ 823 Abs. 1 BGB" can resolve once a parser emits them.
- **Authority.** `doctrine: no_formal_precedent` makes every DE decision `persuasive_precedent`
  ("Persuasive only"); the rationale carries the profile's "To verify" note on § 31 Abs. 1 BVerfGG
  (binding on all constitutional organs, courts and authorities) and § 31 Abs. 2 (force of law).
  Pinned by `tests/test_rechtsprechung_im_internet.py`. The label understates BVerfG decisions; a
  court-level statement of statutory binding effect would be the smallest general change. It was
  not made, because no decision is ingested to justify a core change.
- **Not yet built** (needs real data first): parser, connector, judgment citation format
  ("BGH, Urteil vom 12. März 2024 - VI ZR 123/23, Rn. 15" needs the decision type, which
  `JudgmentCitationInput` does not carry today).

## Known gaps

- No German case law at all; searches for DE return statutes only.
- Licence facts about the site come second-hand (e-Justice Portal), not from its own pages.

## Results (2026-10-14)

- Checks: `ruff check` and `ruff format --check` (122 files) clean, `mypy ulis` clean (77 files);
  full suite against `ulis_test_dejur`: **253 passed** (89 PostgreSQL), including the 3 tests in
  `tests/test_rechtsprechung_im_internet.py` (source gated, robots evidence, authority labels).
- Dev DB `ulis_dejur`: `migrate`; `sync-catalog` → 23 jurisdictions, 16 sources, 4 concepts;
  `ingest de-rechtsprechung-im-internet` → exit 2, "refused: ... source status is 'planned';
  licence review status is 'pending_review'; ... bulk_download ('not_allowed'); no
  connector/parser configured". For comparison `ingest de-gesetze-im-internet` (local cache, no
  requests): 10 documents, 23,265 provision versions, 19,237 passages, 0 judgments;
  `verify-sources`: 10/10 pass.
- Searches (`--jurisdiction DE`), statute hits only, all `applicability_unverified`:
  "vorsätzlich oder fahrlässig Eigentum widerrechtlich verletzt Schaden" → § 823 Abs. 1 BGB;
  "fremde bewegliche Sache wegnimmt zuzueignen" → § 242 Abs. 1 StGB, § 249 Abs. 1 StGB;
  "Kündigung sozial ungerechtfertigt Gründe in der Person" → § 1 Abs. 2 KSchG.
