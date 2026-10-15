# Africa, Asia, Middle East and Oceania: survey of sources (2026-10-14)

Result: **no country in this region could be ingested.** About 200 requests to about 70 hosts (robots.txt first on
each, then terms and format discovery), made with the identifying User-Agent
`ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)`, found for every publisher one of: a robots
or WAF block, an unreachable host, a client-side application with a private API, an API key, terms that forbid
scraping or limit use to non-commercial purposes, or no licence statement at all. Each is recorded in
`config/sources/<id>.yaml` (`planned`, or `rejected` where the publisher forbids it) with the findings and URLs, and
`tests/test_africa_asia_planned.py` checks that the licence gate refuses all of them. Already done elsewhere: JP, TW;
blocked: IN, NZ, HK, PH. No profile was added except the Mauritius stub ([mu.md](mu.md)).

| Country | Source tried (descriptor id) | Finding |
|---|---|---|
| South Africa | gov.za, justice.gov.za (`za-gov-legislation`) | Terms: "only for non-commercial informational or reference purposes"; commercial use needs written permission. Acts are PDF/Word. |
| South Africa | SAFLII (`za-saflii`) | robots.txt answered with a Cloudflare challenge (HTTP 403). |
| South Africa | Constitutional Court (`za-concourt`) | "All Rights Reserved", no terms page found. |
| South Africa, Uganda, Tanzania, Zambia, Nigeria, Ghana, Namibia, Rwanda, Mauritius (also Zimbabwe, Malawi, Sierra Leone) | Laws.Africa platforms: lawlibrary.org.za, ulii.org, tanzlii.org, zambialii.org, nigerialii.org, ghalii.org, namiblii.org, rwandalii.org, mauritiuslii.org (`laws-africa-lii-network`, **rejected**) | One platform (Akoma Ntoso), so one parser could cover all - but the terms say "Scraping and bulk downloading ... is strictly prohibited ... email us if you would like to access our data in bulk"; content is CC BY-NC 4.0; robots.txt carries `Content-Signal: ai-train=no, search=yes, ai-input=no`, disallows judgments and gazettes, names AI crawlers. A written bulk agreement with Laws.Africa is the route. |
| Kenya | Kenya Law (`ke-kenya-law`) | robots.txt: HTTP 403 for ULIS on new.kenyalaw.org and kenyalaw.org. |
| Morocco | SGG Bulletin Officiel (`ma-sgg-legislation`) | Copyright notice only; PDFs. |
| Egypt, Nigeria, Ghana (official) | none | No official consolidated portal could be identified; Nigeria and Ghana appear only on the Laws.Africa platforms above. Not evaluated further. |
| Singapore | Statutes Online (`sg-statutes-online`) | robots.txt: HTTP 403 block page. eLitigation judgments (`sg-elitigation-judgments`): reachable, but the Judiciary's terms reserve all rights and forbid commercial reuse without written permission. |
| South Korea | law.go.kr (`kr-law-go-kr`) | Open API needs a registered key (out of bounds); the website resets ULIS's connections; no keyless bulk download found. |
| Malaysia | lom.agc.gov.my (`my-lom-agc`) | Connection reset. |
| Bangladesh | bdlaws.minlaw.gov.bd (`bd-bdlaws`) | The best technical candidate: robots.txt "Allow: /", static HTML, about 1,630 Acts. But only "Copyright (c) 2019, Legislative and Parliamentary Affairs Division" - no licence or terms. Ask the Division. |
| Pakistan | pakistancode.gov.pk (`pk-pakistan-code`) | Copyright notice, "should not be taken as authentic"; PDFs. |
| Indonesia | jdih.setneg.go.id (`id-jdih-setneg`); Supreme Court directory (`id-mahkamah-agung-putusan`, **rejected**) | JDIH is a client-side app with no terms; peraturan.go.id reset, peraturan.bpk.go.id Cloudflare; the court directory's robots.txt disallows every robot except named search engines and blocks AI crawlers. |
| Vietnam | vanban.chinhphu.vn, congbao.chinhphu.vn (`vn-chinhphu-vanban`) | robots allow; footers ask to name the source when republishing; JS lists, PDF documents, connections reset. |
| Thailand | krisdika.go.th, ocs.go.th, ratchakitcha (`th-krisdika`) | Connection reset, TLS chain failure, HTTP 403. |
| Mongolia | legalinfo.mn (`mn-legalinfo`) | Server-rendered, no robots.txt, but no terms or licence (only a purpose statement); captcha on the API. |
| Nepal | lawcommission.gov.np (`np-law-commission`) | Crawl-delay 10, no terms; PDFs. |
| Sri Lanka | none | srilankalaw.lk is a commercial publisher (Blackhall), lawnet.gov.lk fails TLS, documents.gov.lk is the gazette; no source evaluated further. |
| Israel | Knesset (`il-knesset-legislation`) | Every request redirected to `maintenance-page-geo` (geo-restricted). Nevo is commercial. |
| Turkey | mevzuat.gov.tr, resmigazete.gov.tr, Constitutional Court (`tr-mevzuat`) | Timeouts; the court database is a client-side app. |
| UAE | uaelegislation.gov.ae (`ae-uae-legislation`) | Cloudflare HTTP 403 on pages. |
| Saudi Arabia | laws.boe.gov.sa (`sa-boe-laws`) | Connection reset. |
| Qatar | almeezan.qa (`qa-almeezan`) | React app; data only through a token + reCAPTCHA API, not a public one. |
| Jordan, Lebanon | lob.gov.jo (`jo-lob`), legallaw.ul.edu.lb (`lb-legal-informatics-centre`) | Angular app, no terms; robots.txt HTTP 500, copyright notice. |
| Fiji, Papua New Guinea, Pacific | laws.gov.fj (`fj-laws`), PacLII (`pacific-paclii`) | Angular app; PacLII Cloudflare challenge (HTTP 403). |
| Mauritius | Supreme Court website (`mu-supreme-court-judgments`) | See [mu.md](mu.md): terms allow reproduction of judgments, but robots.txt disallows all robots. |

## Requests that should not have been made

Honest list: `https://ulii.org/api/` (disallowed by that site's robots.txt; HTTP 403 JSON), `https://sso.agc.gov.sg/Help/API`
(after robots.txt had already answered 403), and about 25 pages of supremecourt.govmu.org (listing pages, a few PDFs,
terms) fetched before the last line of its robots.txt (`Disallow: /`) had been noticed. Nothing more was fetched from
those hosts afterwards. The Mauritius PDFs/listing rows kept as test fixtures are labelled in
`tests/fixtures/mu_supreme_court/README.md` and can be deleted.

## Next steps (for the owner)

1. Write to Laws.Africa for a bulk agreement (see `docs/permission-requests.md`): one Akoma Ntoso parser would then cover
   South Africa, Uganda, Tanzania, Zambia, Nigeria, Ghana, Namibia, Rwanda, Mauritius and more.
2. Ask the Bangladesh Legislative and Parliamentary Affairs Division and the Supreme Court of Mauritius for written
   permission; both are technically ready (Bangladesh needs a parser, Mauritius has one).
3. Korea: register for an Open API key as a person, then a Korean-law connector could use it.
4. Languages: Arabic, Indonesian, Turkish and Nepali have PostgreSQL configurations (add to `SEARCH_CONFIGS` only when a
   source is ingested); Korean, Thai, Vietnamese, Mongolian, Hebrew would use `simple` (Korean with the CJK bigrams).
