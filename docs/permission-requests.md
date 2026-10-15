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

Also worth asking, where the answer is not yet in the register: Hong Kong (e-Legislation), Norway (Lovdata's public
texts), Belgium (Belgisch Staatsblad / Justel), Lithuania (e-TAR), the Philippines (Official Gazette / Lawphil), Mexico
(Diputados) and Poland (ISAP's API). Research each publisher's current terms first; the site may already offer an open
data route that makes the request unnecessary.

## When a reply arrives

1. Save the message text and date in the source's `licence.notes` (never paste personal data).
2. If they grant access: set `status: active`, write the manifest and connector as for any source
   ([onboarding-a-jurisdiction.md](onboarding-a-jurisdiction.md)), keep the crawl delay they ask for.
3. If they refuse or do not answer: leave the source `planned`. Do not use mirrors of unclear licence.
