# Launch markets: whose complete laws in force first (2026-10-18)

Question from the owner: "I want the complete laws in force for a few high-value markets." This note ranks the candidates
and names the few. Research aid, not legal advice; market figures are the publishers' or regulators' own and carry their
source; licence readings are the project's, not counsel's ([licence-review.md](../licence-review.md)).

## Method

Four criteria, 0-5 each: (a) legal-market value (revenue, lawyers), (b) legislation data readiness in ULIS today
(full national collection listed from the publisher's own index, licensed, run at least as a slice), (c) licence certainty
for commercial reuse, (d) language and search fit (PostgreSQL text-search configuration, demonstrated ranking quality).
Readiness and licence facts come from the per-country notes under [../jurisdictions/](../jurisdictions/) and
[../corpus.md](../corpus.md); market figures from a web pass on 2026-10-18 (primary sources preferred: statistics offices,
bar associations, regulators; third-party estimates are marked). Blank means no primary figure was found.

## Ranking

| Rank | Market | Legal-market value | Practising lawyers | Legislation in ULIS today | Commercial reuse | a/b/c/d |
|---|---|---|---|---|---|---|
| 1 | US (federal) | ~US$405bn taxable revenue, NAICS 5411, 2024 ([Census via FRED](https://fred.stlouisfed.org/series/REV5411TAXABL144QNSA)) | 1,374,720, 2025 ([ABA](https://www.americanbar.org/news/abanews/aba-news-archives/2025/12/aba-2025-profile-of-the-legal-profession-report/)) | US Code (53 titles) and eCFR (50 titles) listed in full; Code run end to end, eCFR run in full on 2026-10-18 | Public domain, 17 U.S.C. 105; GPO bulk data states no restriction | 5/4/5/5 = 19 |
| 2 | UK | £52.3bn, 2024 ([TheCityUK](https://www.thecityuk.com/news/uk-legal-services-deliver-record-economic-contribution-and-re-enforce-global-influence/)) | 178,428 solicitors, Jul 2026 ([SRA](https://www.sra.org.uk/sra/research-publications/regulated-community-statistics/data/population_solicitors/)) | 7,178 Acts and 17,878 revised instruments listed (every item the site holds as XML); slices run; publisher-dated history available on request | OGL v3.0, commercial use allowed | 4/4/5/5 = 18 |
| 3 | France | €28.9bn, 2021, NAF 691 ([INSEE](https://www.insee.fr/fr/statistiques/7763828?sommaire=7763898)) | 75,681, 1 Jan 2024 ([Ministère de la Justice](https://www.justice.gouv.fr/sites/default/files/2026-06/statistique_profession_avocat_2024.pdf)) | Whole legislative tier (codes, laws, ordonnances) with full history ingested in development, 0 errors; decrees being added; not yet in the launch set before today | Licence Ouverte 2.0 (DILA) | 4/5/5/4 = 18 |
| 4 | Germany | €31.5bn, 2021, WZ 69.1 ([Destatis](https://www.destatis.de/DE/Themen/Wirtschaft/Preise/Erzeugerpreisindex-Dienstleistungen/Methoden/brancheninformationen.html)) | 166,504, 1 Jan 2025 ([BRAK](https://www.brak.de/presse/zahlen-und-statistiken/statistiken/archiv-statistiken-der-brak/)) | 6,139 federal laws and ordinances listed from the publisher's own index; 1,101-law trial, 0 errors; current consolidation only (no history) | Free use and re-use stated by the publisher; § 5 UrhG | 4/4/5/4 = 17 |
| 5 | Canada (federal) | about C$10bn, derived from 22,573 offices x average revenue, 2024 ([ISED](https://www.ised-isde.canada.ca/app/ixb/cis/performance/rev/54111)) | 114,574 practising, 2024 ([FLSC](https://flsc.ca/wp-content/uploads/2025/09/FederationStatisticsReport2024.pdf)) | All 953 federal Acts EN+FR run, 0 errors; regulations being added | Reproduction of Federal Law Order SI/97-5; OGL-Canada | 3/4/5/5 = 17 |
| 6 | EU | no EU-wide primary figure reached (DE+FR+ES+IT national figures above sum to about €90bn) | CCBE table not reached | 26,878 acts in force with English Formex listed (of 35,085); the rest being added where a text format exists; 108-work slice run | Decision 2011/833/EU via Cellar | 4/3/4/5 = 16 |
| 7 | Australia (federal) | A$33.6bn, 2024-25 (IBISWorld, third party) | 97,500 solicitors, Oct 2024 ([Law Society of NSW](https://www.lawsociety.com.au/sites/default/files/2025-06/2024%20National%20Profile%20of%20Solicitors%20-%20Final.pdf)) | 785 principal Acts with a compilation listed; the 477 never-amended Acts being added; legislative instruments not listed | CC BY 4.0 | 3/3/5/5 = 16 |
| 8 | Spain | €13.9bn, 2022 (Eurostat relayed by a third party) | 149,695 ejercientes, Feb 2025 ([Legal Today](https://www.legaltoday.com/revista-aja/1015/articulos/22/index.html)) | 6,826 State norms with full history listed; slice run; autonomous-community law not listed; ranking weak in Spanish | AEBOE reuse conditions, commercial allowed | 3/4/5/3 = 15 |
| 9 | Japan | about US$5.5bn, 2024 (third party) | 45,808, 31 Mar 2024 ([MOJ](https://www.moj.go.jp/content/001461557.pdf)) | 8,989 laws in force listed; 35-law slice run; bigram search without morphology | Public Data License 1.0 | 3/3/5/2 = 13 |
| 10 | Netherlands | no euro total found | 18,723, 1 Jan 2025 ([NOvA](https://www.advocatenorde.nl/sites/default/files/2025-03/nova_balie_in_beeld_2025_def.pdf)) | 43,430 regulation ids listed; 319-sample run; 85 h load | CC0, but KOOP's stance on AI crawlers is an open question | 2/3/3/4 = 12 |
| 11 | Switzerland | not found | 12,967, 2020 ([SAV/FSA](https://www.sav-fsa.ch/documents/672183/2070367/07arv0620.pdf)) | 7-enactment pilot; builder exists | Art. 5 URG free; Art. 49(1)(h) PublO limits paid redistribution | 2/1/3/4 = 10 |
| 12 | Italy | over €16bn, 2024 ([Censis/Cassa Forense](https://www.altalex.com/documents/news/2026/05/07/rapporto-censis-cassa-forense-pil-avvocatura-cresce-piu-pil-italiano)) | 228,641, 2025 ([Cassa Forense](https://www.cassaforense.it/media/munf4vli/rapporto-avvocatura-2025.pdf)) | 41 works; the act lists sit behind robots-disallowed search pages | Unknown: Normattiva's legal notice is silent on commercial use; the gate refuses it | 4/1/1/3 = 9 |
| 13 | India | no reconcilable figure | about 1.64 million enrolled, 2019 ([Rajya Sabha answer](https://legalaffairs.gov.in/sites/default/files/USQ%201221%20for%2028%20Nov%202019.pdf)) | Nothing: every official host answers 403 or resets ([india.md](india.md)) | Unknown | 3/0/0/5 = 8 |
| 14 | Brazil | no aggregate found | 1,512,953 ([OAB register](https://www.oab.org.br/institucionalconselhofederal/quadroadvogados), read 2026-10-18) | Nothing for legislation: Planalto, LexML and Senado refuse or reset the bot | Terms unread | 3/0/2/3 = 8 |
| 15 | China | no official total found | 651,600, end 2022 ([People's Daily](https://paper.people.com.cn/rmrb/html/2023-06/15/nw.D110000renmrb_20230615_3-14.htm)) | Constitution only | No: gov.cn forbids commercial reposting | 4/0/0/1 = 5 |
| 16 | South Korea | about KRW 8tn, 2024 (press estimate) | 37,368, Jul 2025 ([Law Times](https://www.lawtimes.co.kr/news/208462)) | Nothing: the Open API needs a person-registered key | Unknown | 2/0/1/1 = 4 |

The research incumbents are the same everywhere ULIS can load data: Thomson Reuters (Legal Professionals US$2.9bn revenue
2024), RELX (LexisNexis) and Wolters Kluwer, plus national houses (beck-online and juris, Dalloz and Lefebvre, Westlaw
Japan, Aranzadi and vLex, Giuffrè). Open-data legislation is the cost-free wedge against all of them; it is no wedge where
the publisher blocks the bot.

## The few: five markets, in this order

1. **United States (federal).** Largest market, cleanest licence, English, and the one collection run end to end. Complete
   when the US Code and eCFR loads are run on the server and `/v1/coverage` is published. State law is a separate,
   mostly blocked project ([../jurisdictions/us-states.md](../jurisdictions/us-states.md)).
2. **United Kingdom.** Second English market, OGL v3.0 with explicit commercial reuse, and the only collection with
   publisher-dated point-in-time history. Complete when the never-run server load finishes (Acts about 10 h, then
   instruments about 25 h with `current_only`; full history on request, 39 h + 68 h). About 100,000 pre-1988 and local
   items exist only as PDF on the site and stay out (no OCR).
3. **France.** The best-finished data asset in the repository (whole legislative tier with full history, 0 errors) under
   Licence Ouverte 2.0. Complete when decrees are included, the stock pin is current and the increments are applied,
   and the server load has run. In the launch set from today.
4. **Germany.** Largest continental market; free-use statement and § 5 UrhG. Complete when the 8.5 h full load has run.
   No point-in-time history exists at the publisher: every answer says so.
5. **European Union.** Cross-cutting for every EU customer; Cellar's reuse decision covers it. Complete when the acts without
   an English Formex file are listed where a text format exists and the long Cellar load (about 37 h for the Formex set)
   has run. In the launch set from today; fifth, not a substitute for a national market.

Canada and Australia keep loading in the default go-live and are being completed in the same batch (federal regulations;
never-amended principal Acts), so they are offered as complete too. Spain, Sweden, Finland and Japan load by default
because their national collections are listed in full and licensed, but are not marketed first (ranking quality in
Spanish, Japanese morphology, market size).

## Deferred, with reasons

- **India, Brazil, South Korea, China:** nothing can be loaded (hosts answer 403 or reset; an API key tied to a person;
  China forbids commercial reposting). Owner-level outreach only ([../permission-requests.md](../permission-requests.md)).
- **Italy:** commercial use unknown and no permitted way to enumerate the ordinary laws; wait for a written answer.
- **Switzerland:** pilot only; Art. 49(1)(h) PublO limits paid redistribution to value-added form; small market.
- **Netherlands:** CC0, but the publisher's stance on AI crawlers is an open question for a model-backed product; 85 h load.

## Evidence gaps

No primary 2023-2024 revenue figure was found for Germany (2021 used), France (2021), Japan, Canada (derived), Spain
(third-party relay), the Netherlands, Switzerland, India, Brazil or China; CCBE and Eurostat totals were not reachable
from this environment. Every licence reading is the project's own; [../go-live.md](../go-live.md) requires a lawyer's
check before taking money.
