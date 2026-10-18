# Permission requests for blocked sources

These are **drafts for the owner to send**; ULIS never contacts a publisher itself. The law is public everywhere
listed here. What stands in the way is the access policy of the website or API (robots.txt, an API key, a challenge page)
or a missing statement of reuse terms for the publisher's compilation. ULIS does not work around a block, so each of
these waits for a written answer. Keep every reply: add the sender, date and wording to the source's YAML in
`config/sources/` (`licence` block), switch `status` to `active` and add the manifest.

Contact addresses are not listed here on purpose: take them from each publisher's own contact page.

Fill in the placeholders: `[NAME]`, `[ORGANISATION]`, `[EMAIL]`, `[PUBLIC URL OF THE SERVICE]`. Sending from an address on
your own domain, with a page describing the service, gets answers faster than a free-mail address.

## Common text (adapt the opening line for each publisher)

> Subject: Request to use [publisher/service] legislation data for a non-commercial legal research service
>
> Dear [publisher],
>
> We operate a legal research aid ("ULIS", [PUBLIC URL OF THE SERVICE]) that lets lawyers search the text of
> legislation and court decisions and read the exact source wording with a link back to the official publication. We
> never alter the text; every result names [publisher] as the source and states that the official publication is
> authoritative. We would like to download [the Acts and instruments / the decisions] listed at [URL] about once a
> week, at no more than one request every [5] seconds, identifying ourselves as
> `ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)`.
>
> Your site currently [blocks automated access / states no licence / requires an API key]. May we:
> 1. retrieve the data automatically as described (or may you point us to a bulk download or an API key we should use);
> 2. store and display the text, with attribution, in our service; and
> 3. do so under which licence or terms (please name them)?
>
> Thank you, [NAME], [ORGANISATION], [EMAIL]

## One request per source

| Source (id) | Who to ask | What to say in the opening line | Why it is blocked now |
|---|---|---|---|
| `in-india-code` | Legislative Department, Ministry of Law and Justice, Government of India (via the India Code contact page) | Central Acts, rules and regulations on India Code | The site answers automated requests with HTTP 403; its terms were not readable; section 52(1)(q) of the Copyright Act 1957 needs counsel's reading |
| `nz-pco-legislation` | Parliamentary Counsel Office, New Zealand (legislation.govt.nz contact form) | New Zealand Acts, secondary legislation and their versions | The API needs a key and returns metadata only; the XML sits behind a challenge page; the copyright page was not readable |
| `at-ris-bundesrecht`, `at-ris-judikatur` | Bundeskanzleramt, RIS team (use the contact address on their website) | Consolidated federal law and published decisions in the RIS | CC BY 4.0, but the API host's robots.txt disallows every robot; ask in writing whether the OGD API may be used by this bot and at what rate |
| `de-rechtsprechung-im-internet` | Bundesamt für Justiz (use the contact address on their website) | Federal court decisions on rechtsprechung-im-internet.de | The decisions are free of copyright (§ 5 UrhG), but robots.txt disallows every robot except the European e-Justice crawler |
| `gb-find-case-law` | The National Archives, Find Case Law (use the contact address on their website) | Judgments in the Find Case Law service, bulk | Computational analysis needs a separate licence application; ask for the "computational analysis" licence and its terms |
| `ie-revised-acts-lrc` | Law Reform Commission, Ireland (use the contact address on their website) | The Revised Acts (administrative consolidations) | Only a copyright line, no reuse licence; the Oireachtas PSI licence is not assumed to cover it |

## Added after the second collection round

All of these are `planned` or `rejected` in `config/sources/` with the publisher's own wording (see
[corpus.md](corpus.md)). The data is public; the website's robots.txt, error response or terms stand in the way.

| Source (id) | Who to ask | Why it is blocked now |
|---|---|---|
| `us-caselaw-access-project` | Harvard Library Innovation Lab (static.case.law) | Data is CC0, but `robots.txt` on the data host disallows all robots; ask for an identified-bot exception or a bulk copy |
| `us-courtlistener-bulk` | Free Law Project (CourtListener) | `robots.txt` returned 403 twice; ask about their bulk-data files |
| `coe-echr-hudoc` | Council of Europe, European Court of Human Rights registry | Terms allow private/information use only; other use needs written permission; the CoE sites answer with a Cloudflare 403 |
| `icj-cij` | International Court of Justice registry | Terms: no commercial use |
| `ca-scc-decisions` | Supreme Court of Canada (Judgments) | CAPTCHA 403 on the fifth request; ask for a feed or bulk copy |
| `au-hca-judgments` | High Court of Australia | Connection dropped or Cloudflare challenge |
| `laws-africa-lii-network` | Laws.Africa (AfricanLII platforms: South Africa, Uganda, Tanzania, Zambia, Nigeria, Ghana, Namibia, Rwanda, Mauritius) | Terms forbid scraping and bulk download, content CC BY-NC; one Akoma Ntoso parser would cover them all if bulk access is granted |
| `ca-bc-bclaws`, `ca-ab-kings-printer`, `ca-qc-legisquebec` | Queen's/King's Printers of British Columbia, Alberta, Québec | `robots.txt` disallows (BC, Alberta); HTTP 403 (Québec) |
| `au-nsw-legislation`, `au-sa-legislation`, `au-act-legislation`, `au-wa-legislation` | Parliamentary Counsel's Offices of NSW, South Australia, the ACT and Western Australia | HTTP 403 (NSW, SA); licence unclear or unreadable (ACT, WA) |
| `us-ca-leginfo`, `us-ny-laws`, `us-pa-consolidated` | California Legislative Counsel, New York Legislative Bill Drafting Commission, Pennsylvania Legislative Reference Bureau | Cloudflare challenge on robots.txt (CA); API key and challenge page (NY); no reuse terms (PA, the best next state) |
| `us-wa-rcw`, `us-tx-statutes` (active, ask to be safe) | Washington Statute Law Committee; Texas Legislative Council | Both claim copyright in the compilation and state no terms for automated or commercial use (Washington asks to be contacted before the code is sold) |
| `cl-bcn-leychile` | Biblioteca del Congreso Nacional de Chile | API key required; terms: CC BY-NC-ND and no mass extraction without authorisation |
| `hu-njt`, `ro-portal-legislativ`, `bg-dv-parliament`, `gr-et-fek`, `mt-legislation` | Hungarian, Romanian, Bulgarian, Greek and Maltese official gazettes | Connection closed or timed out; PDF only; no reuse terms |

## From the bulk-dataset survey (2026-10-15)

See [bulk-datasets-survey.md](bulk-datasets-survey.md) for the evidence. These are the requests that would unlock the most:

| Who | What to ask | Why |
|---|---|---|
| Free Law Project (CourtListener) | Written OK for an identified bot (`ULIS-research-bot/0.1`) to download the bulk files from their public S3 bucket (`bulk-data/`), and confirmation that the "free of known copyright restrictions" statement allows storage and display in a research service | First US case law beyond the Supreme Court and federal appeals courts; the website refuses our bot (403), so nothing is downloaded until they answer |
| The National Archives (UK) | The free licence for computational analysis of Find Case Law | UK judgments; automated processing is not allowed until the licence is held |
| Judicial Yuan (Taiwan) | An open-data account for the judgments service, and its terms for a service that displays judgments | Access needs an account the operator registers and a 00:00-06:00 service window |
| Superior Tribunal de Justiça (Brazil) and Corte costituzionale (Italy) | Confirmation that their CC BY / CC BY-SA 3.0 open data may be stored and displayed; for Italy, whether share-alike reaches the ULIS database | Two court-decision sources that passed the survey (share-alike needs counsel) |

Also worth asking, where the answer is not yet in the register: Hong Kong (e-Legislation), Norway (Lovdata's public
texts), Belgium (Belgisch Staatsblad / Justel), Lithuania (e-TAR), the Philippines (Official Gazette / Lawphil), Mexico
(Diputados) and Poland (ISAP's API). Research each publisher's current terms first; the site may already offer an open
data route that makes the request unnecessary.

## Notices and faster routes for active sources (rate research, 2026-10-18)

These sources are active and load at the pace each publisher allows (see `rate_limits` in their
`config/sources/<id>.yaml`). Two publishers ask to be contacted first; the drafts below are for the owner to send from
the route the publisher's own pages name. Until a written answer arrives, the pace stays as it is.

### Federal Register of Legislation (Australia): notice before a full crawl

The Register's data-reuse page asks: "Before doing a full site crawl or anything else that may use significant system
resources, it is advisable to contact us 1-2 weeks beforehand, to ask about any planned outages or other issues that
may be relevant. It is preferable for any further incremental crawls of this website to be scheduled to occur outside
the hours of 0800 to 2000 Australian time (UTC +10). Failing this, users who impose a large load on this website or
otherwise interfere with its operation may have their access to this website blocked without notice by automated
processes." (https://www.legislation.gov.au/help-and-resources/using-the-legislation-register/data-share-and-reuse,
read 2026-10-18). Read strictly, the notice is needed before every full load of `au-federal-register` and, above all,
of `au-federal-register-instruments`.

- **Route**: the Register's feedback form, "Contact the Federal Register of Legislation"
  (https://www.legislation.gov.au/help-and-resources/using-the-legislation-register/feedback), which the reuse page
  names for API feedback. The API description (https://api.prod.legislation.gov.au/swagger/v1/swagger.json,
  `info.contact`) also names a contact address; take it from there.
- **When**: 1-2 weeks before the planned dates.
- **Keep meanwhile**: robots.txt's Crawl-delay of 10 s, one connection, the off-peak window the connector already
  keeps (`avoid_hours`: outside 08:00-20:00 UTC+10; during Australian daylight saving, October to April, also avoid
  07:00-08:00 UTC+10, so run 10:00-21:00 UTC), and never the two Register sources (or any other legislation.gov.au
  fetch) at the same time.

> Subject: Planned automated download of Commonwealth legislation (notice as asked on your data-reuse page)
>
> Dear Federal Register of Legislation team,
>
> As your data-reuse page asks, we are writing [1-2 weeks] before a full download from the Register. We operate
> "ULIS" ([PUBLIC URL OF THE SERVICE]), a legal research aid that shows the exact text of legislation with a link back to
> the Register and the statement that the Register is the authoritative source; content is used under CC BY 4.0 with
> attribution.
>
> - Client: one connection, identified as `ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)`,
>   to https://api.prod.legislation.gov.au (Titles, Versions and Documents endpoints, EPUB).
> - Pace: one request every 10 seconds (your robots.txt Crawl-delay), only outside 08:00-20:00 Australian time.
> - Volume: about 1,443 requests (the principal Acts in force: about 4 hours), and separately about 24,753 requests
>   for the legislative instruments in force (about 69 hours, spread over several nights).
> - Planned dates: [DATES AND TIMES, UTC and AEST/AEDT].
>
> Could you tell us about any planned outages or other issues in that period? If a faster route would suit you
> better - a shorter delay, a second connection, or a bulk export - we would be glad to use it on your terms; until we
> hear from you we keep to the pace above. Later refreshes fetch only new compilations (tens of requests).
>
> Thank you, [NAME], [ORGANISATION], [EMAIL]

### legislation.gov.uk (The National Archives): a faster or bulk route

robots.txt sets "Crawl-delay: 5" and the fair-use policy says "Follow the crawl-delay setting in the robots.txt file"
and "You must not exceed the request rate limit of 1,500 requests in any 5-minute period"
(https://www.legislation.gov.uk/fair-use-policy, read 2026-10-18). For "a large, one-off crawl of all or part of the
content" it says "you should use a different way to download content"; the ways it names are the New Legislation feed
and the Publication Log feed (for new and changed items, which suits refreshes but not a first load). And: "If you
believe that: either this policy or the rules in the robots.txt file may prevent you from using, crawling or scraping
legislation.gov.uk as you require, or your activity may have an impact on the performance or availability of
legislation.gov.uk or its API, please contact us prior to taking any action." The bulk downloads at
research.legislation.gov.uk answer "By Invitation Only" (HTTP 401, 2026-10-18).

- **Route**: the "contact us" link in the policy's "Talk to us if you have concerns" section
  (https://www.legislation.gov.uk/fair-use-policy); the same team address is given on
  https://legislation.github.io/data-documentation/. Take the address from those pages.
- **When**: before the first full load of the three legislation.gov.uk sources, and before any change of rate or
  number of connections.
- **Keep meanwhile**: 5 s between request starts, one connection, the three sources never at the same time.
- **For refreshes** (no permission needed): the Publication Log feed the policy recommends, so that only changed items
  are fetched.

> Subject: legislation.gov.uk data: one-off load at your Crawl-delay, and access to the bulk downloads
>
> Dear legislation.gov.uk data team,
>
> Your fair-use policy asks users planning a large crawl to contact you first, so we are writing before we start. We
> operate "ULIS" ([PUBLIC URL OF THE SERVICE]), a legal research aid that shows the exact text of legislation, with
> attribution under the Open Government Licence v3.0 and a link back to legislation.gov.uk.
>
> - Client: one connection, identified as `ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)`,
>   requesting the Akoma Ntoso XML (`data.akn`) of each item.
> - Pace: one request every 5 seconds (your robots.txt Crawl-delay; 60 requests per 5 minutes).
> - Volume: about 7,278 requests for the current text of the Acts, Measures and Orders in Council (about 10 hours;
>   about 28,200 requests with every earlier version), and, one after the other, the statutory instruments that have
>   versions (17,931 works) and those that exist as made only (65,456 works): about 126 hours in all at this pace.
>
> 1. Is this one-off load at the Crawl-delay acceptable to you, or would you prefer another route or time window?
> 2. May we have an invitation to the bulk downloads at research.legislation.gov.uk? If so: are the revised
>    (latest-version) texts there in Akoma Ntoso, what are the terms, and how often are the packages refreshed?
> 3. If bulk access is not possible, may we use a shorter crawl delay (for example [1] second) or a second connection
>    for our User-Agent, within your limit of 1,500 requests per 5 minutes?
>
> After the first load we will follow the Publication Log feed and fetch only items that changed. Until we hear from
> you we keep to the pace above.
>
> Thank you, [NAME], [ORGANISATION], [EMAIL]

### Other publishers: optional courtesy notes and an open question

None of these is a publisher requirement; the research found no notice or permission step. They are worth a short
note (the pace, the single connection, the User-Agent and the volume) before the first full run at the new pace.

| Source (id) | Pace now | Route (from the publisher's own pages) | Note |
|---|---|---|---|
| `eu-eur-lex` | 1 s, one connection | Cellar helpdesk, https://op.europa.eu/en/web/cellar/contact | About 70,000 requests to publications.europa.eu; ask whether they prefer a lower rate or off-peak hours. The EUR-Lex Data Dump needs EU Login; a person should read its terms in a browser before ULIS uses it |
| `es-boe-consolidada` | 1 s, one connection | Webmaster form linked from the API page ("Ayúdenos a mejorar"), https://www.boe.es/contactar/formulario_simple.php?tipo=WEB | 6,827 requests; AEBOE may suspend access "sin previo aviso" for breach of its conditions |
| `de-gesetze-im-internet` | 1 s, one connection | Kompetenzzentrum Rechtsinformationssystem des Bundes, named on https://www.gesetze-im-internet.de/impressum.html | 6,141 requests; ask there before more than one connection or more than 1 request/s |
| `se-riksdagen-sfs` | 1 s, one connection | Riksdagsinformation, named on the open-data pages | 5,353 requests |
| `jp-egov-laws` | 5 s, unchanged | e-Gov inquiry form linked from https://laws.e-gov.go.jp/apitop/ | **Ask before** any full API loop or a faster pace: Digital Agency asked users not to loop over every law through the API (https://www.digital.go.jp/policies/legal-practice/public-test). The official bulk download (all_xml.zip) needs no request |

## When a reply arrives

1. Save the message text and date in the source's `licence.notes` (never paste personal data).
2. If they grant access: set `status: active`, write the manifest and connector as for any source
   ([onboarding-a-jurisdiction.md](onboarding-a-jurisdiction.md)), keep the crawl delay they ask for.
3. If they refuse or do not answer: leave the source `planned`. Do not use mirrors of unclear licence.
