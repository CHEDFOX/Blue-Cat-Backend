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
`config/sources/<id>.yaml`). Two publishers ask to be contacted first; the drafts below are complete and ready for the
owner to send through the route each publisher's own pages name. Until a written answer arrives, the pace stays as it
is.

### Federal Register of Legislation (Australia): notice before a full crawl

The Register's data-reuse page asks: "Before doing a full site crawl or anything else that may use significant system
resources, it is advisable to contact us 1-2 weeks beforehand, to ask about any planned outages or other issues that
may be relevant. It is preferable for any further incremental crawls of this website to be scheduled to occur outside
the hours of 0800 to 2000 Australian time (UTC +10). Failing this, users who impose a large load on this website or
otherwise interfere with its operation may have their access to this website blocked without notice by automated
processes." (https://www.legislation.gov.au/help-and-resources/using-the-legislation-register/data-share-and-reuse,
read 2026-10-18). Read strictly, the notice is needed before every full load of `au-federal-register` and, above all,
of `au-federal-register-instruments`.

- **Route**: the form on the Register's Feedback page,
  https://www.legislation.gov.au/help-and-resources/using-the-legislation-register/feedback (the reuse page's "contact
  us" link goes there; fields: name, email, subject, details, system details). The only published email address is
  feedback@legislation.gov.au, from `info.contact` of https://api.prod.legislation.gov.au/swagger/v1/swagger.json; the
  notice paragraph names the form, so use the form (read 2026-10-21).
- **When**: 1-2 weeks or more before the planned start. The draft below plans Monday 16 November 2026; if that date
  moves, send a one-line update with the new date.
- **Keep meanwhile**: robots.txt's Crawl-delay of 10 s, one connection, the off-peak window the connector already
  keeps (`avoid_hours`: outside 08:00-20:00 UTC+10; during Australian daylight saving, October to April, also avoid
  07:00-08:00 UTC+10, so run 10:00-21:00 UTC), and never the two Register sources (or any other legislation.gov.au
  fetch) at the same time.

Form fields: Your name: ULIS team. Your email: the address you read. Subject and Details:

> Subject: Planned automated download of Commonwealth legislation from 16 November 2026 (notice as asked on your
> data-reuse page)
>
> Dear Federal Register of Legislation team,
>
> As your data share and reuse page asks, we are writing before a full download from the Register.
>
> We are building ULIS, a legal research service that lets lawyers search the exact text of the laws in force, with a
> link back to the Register and a statement that the Register is the authoritative source. We use the content under
> CC BY 4.0, with attribution. The service is not public yet; the project page is
> https://github.com/CHEDFOX/Blue-Cat-Backend.
>
> What we plan:
>
> - Client: one connection at a time, identified by the User-Agent "ULIS-research-bot/0.1
>   (+https://github.com/CHEDFOX/Blue-Cat-Backend)", to https://api.prod.legislation.gov.au (the Titles, Versions and
>   Documents endpoints, EPUB documents).
> - Pace: one request every 10 seconds, the Crawl-delay in your robots.txt.
> - Hours: only between 10:00 and 21:00 UTC, which is 21:00 to 08:00 in Canberra and Sydney (AEDT), outside 08:00 to
>   20:00 Australian time.
> - Volume: about 1,440 requests for the principal Acts in force, about 4 hours, in one night.
> - Start: Monday 16 November 2026, at 10:00 UTC (21:00 AEDT).
> - Later updates fetch only new compilations, a few dozen requests at a time, in the same hours.
> - We may later also download the legislative instruments in force (about 24,750 requests, spread over about seven
>   nights). We will write to you again before we do.
>
> Could you let us know of any planned outages or other issues in that period? If a faster route suits you better (a
> shorter delay, a second connection or a bulk export), we would be glad to use it on your terms. Until we hear from
> you, we will keep to the pace above.
>
> Thank you,
> The ULIS team

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

- **Route**: email data.legislation@nationalarchives.gov.uk, the "contact us" address of the fair-use policy's "Talk to
  us if you have concerns" section (https://www.legislation.gov.uk/fair-use-policy) and of the data documentation
  (https://legislation.github.io/data-documentation/), read 2026-10-21.
- **When**: before the first full load of the three legislation.gov.uk sources, and before any change of rate or
  number of connections. The draft below plans Monday 16 November 2026.
- **Keep meanwhile**: 5 s between request starts, one connection, the three sources never at the same time.
- **For refreshes** (no permission needed): the Publication Log feed the policy recommends, so that only changed items
  are fetched.

> To: data.legislation@nationalarchives.gov.uk
>
> Subject: Planned one-off download of legislation.gov.uk data from 16 November 2026, and access to the bulk downloads
>
> Dear legislation.gov.uk data team,
>
> Your fair-use policy asks anyone planning a large crawl to contact you first, so we are writing before we start.
>
> We are building ULIS, a legal research service that lets lawyers search the exact text of legislation, with
> attribution under the Open Government Licence v3.0 and a link back to legislation.gov.uk. The service is not public
> yet; the project page is https://github.com/CHEDFOX/Blue-Cat-Backend.
>
> What we plan:
>
> - Client: one connection at a time, identified by the User-Agent "ULIS-research-bot/0.1
>   (+https://github.com/CHEDFOX/Blue-Cat-Backend)", requesting the Akoma Ntoso XML (data.akn) of each item.
> - Pace: one request every 5 seconds, the Crawl-delay in your robots.txt: 60 requests per 5 minutes, well under your
>   limit of 1,500.
> - Volume, current text only, one part after the other:
>   - Acts, Measures and Orders in Council: about 7,280 requests (about 10 hours);
>   - statutory instruments with revised versions: about 18,510 requests (about 26 hours);
>   - statutory instruments that exist only as made: about 68,450 requests (about 95 hours).
>
>   In all about 94,240 requests over about 5.5 days.
> - Start: Monday 16 November 2026.
> - Afterwards we will follow the Publication Log feed and fetch only items that changed.
>
> Our questions:
>
> 1. Is this one-off load at your Crawl-delay acceptable to you, or would you prefer another route or time window?
> 2. Could we have an invitation to the bulk downloads at research.legislation.gov.uk? If so, do they include the
>    revised (latest-version) texts in Akoma Ntoso, on what terms, and how often are they refreshed?
> 3. If bulk access is not possible, may we use a shorter delay for our User-Agent, for example one request per
>    second, or a second connection, within your limit of 1,500 requests per 5 minutes?
>
> Until we hear from you, we will keep to the pace above.
>
> Thank you,
> The ULIS team

### Other publishers: optional courtesy notes and an open question

None of these is a publisher requirement; the research found no notice or permission step. They are worth a short
note (the pace, the single connection, the User-Agent and the volume) before the first full run at the new pace.

| Source (id) | Pace now | Route (from the publisher's own pages) | Note |
|---|---|---|---|
| `eu-eur-lex` | 1 s, one connection | Cellar helpdesk, https://op.europa.eu/en/web/cellar/contact | About 70,000 requests to publications.europa.eu; ask whether they prefer a lower rate or off-peak hours. The EUR-Lex Data Dump needs EU Login; a person should read its terms in a browser before ULIS uses it |
| `es-boe-consolidada` | 1 s, one connection | Webmaster form linked from the API page ("Ayúdenos a mejorar"), https://www.boe.es/contactar/formulario_simple.php?tipo=WEB | 6,827 requests; AEBOE may suspend access "sin previo aviso" for breach of its conditions |
| `de-gesetze-im-internet` | 1 s, one connection | Kompetenzzentrum Rechtsinformationssystem des Bundes, named on https://www.gesetze-im-internet.de/impressum.html | 6,141 requests; ask there before more than one connection or more than 1 request/s |
| `se-riksdagen-sfs` | 1 s, one connection | Riksdagsinformation, named on the open-data pages | 5,353 requests |
| `jp-egov-laws` | 5 s, unchanged | e-Gov inquiry form linked from https://laws.e-gov.go.jp/apitop/ | **Ask before** any full API loop or a faster pace: Digital Agency asked users not to loop over every law through the API (https://www.digital.go.jp/policies/legal-practice/public-test). Since 2026-10-18 ULIS loads every law from the official bulk download (all_xml.zip, no key, registration or notice) plus 50 API requests, so no request is needed |

## When a reply arrives

1. Save the message text and date in the source's `licence.notes` (never paste personal data).
2. If they grant access: set `status: active`, write the manifest and connector as for any source
   ([onboarding-a-jurisdiction.md](onboarding-a-jurisdiction.md)), keep the crawl delay they ask for.
3. If they refuse or do not answer: leave the source `planned`. Do not use mirrors of unclear licence.
