# Remaining European states - evaluation (2026-10-14/15)

Order of the evaluation: Slovenia, Croatia, Hungary, Romania, Bulgaria, Greece, Cyprus, Malta, Iceland, Liechtenstein, Ukraine, Serbia,
North Macedonia, Moldova, Georgia, Armenia, Turkey. For each publisher the `robots.txt` and the publisher's own terms were read first (identified client
`ULIS-research-bot/0.1`, one request at a time); nothing was worked around. Norway, Belgium and Lithuania were already recorded as blocked and not retried.
Memberships (EU for SI, HR, CY, MT, HU, RO, BG, GR; EEA for IS and LI) are left to the lead.

| State | Source | Result | Why (details in `config/sources/<id>.yaml`) |
|---|---|---|---|
| Slovenia | Uradni list RS archive (`si-uradni-list`) | **ingested** ([si.md](si.md)) | the gazette's terms exempt its content from their restrictions; robots.txt only `/_pdf/*/*a/` |
| Slovenia | PISRS (`si-pisrs`) | planned | terms forbid reproducing the database; the API needs an X-API-Key requested by a person |
| Croatia | Narodne novine (`hr-narodne-novine`) | **ingested** ([hr.md](hr.md)) | open, free, crawlers and automated services invited (3 requests/s); art. 18(3) of the Copyright Act |
| Hungary | njt.hu (`hu-njt`) | planned | host closes every HTTPS connection without a response; Magyar Közlöny is PDF only |
| Romania | legislatie.just.ro (`ro-portal-legislativ`) | planned | host (and its SOAP service) closes every HTTPS connection without a response |
| Bulgaria | dv.parliament.bg (`bg-dv-parliament`) | planned | gazette as published only, no terms found, an empty body for a direct material request; lex.bg is a commercial site behind a bot challenge |
| Greece | et.gr / e-nomothesia (`gr-et-fek`) | planned | gazette is PDF only; e-nomothesia is a private site (Crawl-delay 30) |
| Cyprus | CyLaw (`cy-cylaw`) | planned (terms forbid) | terms prohibit indexing by robots and systematic or automated download without written consent |
| Malta | legislation.mt (`mt-legislation`) | planned | texts are PDF only; copyright notice of the Attorney General's office; no reuse terms. ELI sitemap lists 21,662 addresses |
| Iceland | althingi.is lagasafn (`is-althingi-lagasafn`) | planned | Cloudflare bot challenge (HTTP 403) on the identified client |
| Liechtenstein | Lilex gesetze.li (`li-lilex`) | **ingested** ([li.md](li.md)) | no robots.txt or terms; laws are not protected by copyright (URG Art. 5); versions back to 1921 for the Verfassung |
| Ukraine | zakon.rada.gov.ua, data.rada.gov.ua (`ua-zakon-rada`) | planned (robots refuse) | `User-Agent: * / Disallow: /` on both hosts |
| Serbia | pravno-informacioni-sistem.rs (`rs-pravno-informacioni-sistem`) | planned | JavaScript application, no machine text or terms; ELI paths allowed by robots.txt |
| North Macedonia | slvesnik.com.mk (`mk-sluzben-vesnik`) | planned | TLS chain not verifiable (certificate checks are not disabled) |
| Moldova | legis.md (`md-legis`) | planned | Cloudflare bot challenge (HTTP 403) |
| Georgia | matsne.gov.ge (`ge-matsne`) | planned | codified texts need registration, part is fee-based, rights reserved (robots.txt allows documents, Crawl-delay 10) |
| Armenia | arlis.am (`am-arlis`) | planned | "Copyright reserved", no terms, 1.4-2.8 MB pages; no robots.txt |
| Turkey | mevzuat.gov.tr (`tr-mevzuat`) | planned | host times out or resets the connection; resmigazete.gov.tr prints acts as published only |

## PostgreSQL text-search configurations (`ulis/languages.py`)

Available in PostgreSQL 16 here: arabic, armenian, basque, catalan, danish, dutch, english, finnish, french, german, greek, hindi, hungarian, indonesian, irish, italian,
lithuanian, nepali, norwegian, portuguese, romanian, russian, serbian, spanish, swedish, tamil, turkish, yiddish. Of the sources above: Liechtenstein uses `german`;
Croatia and Slovenia have **no** configuration and use `simple` (no stemming - inflected forms match only when identical: a real weakness for these highly inflected
languages, stated in each profile). Not available and `simple` when added: Bulgarian, Ukrainian, Icelandic, Maltese, Macedonian, Georgian. Available when the source opens:
Hungarian, Romanian (also Moldova), Greek, Turkish, Armenian, Serbian (Cyrillic and Latin script not verified). `ulis/languages.py` already maps hu, ro, el, tr, ru, no; it has no
entry for hy or sr.

## How to extend

Retry the unreachable hosts (HU, RO, TR, IS, MD) from the owner's network and read their terms; ask the publishers named in the source files for permission where the
terms or robots.txt refuse (SI PISRS, CY, UA, GE, IS, MD, MT); the generic helpers `ulis/sources/parsers/legal_html.py` (tiny DOM, numbered-article reader) and
`ulis/sources/connectors/site_session.py` (manifest check, robots, polite session) serve any further HTML source.
