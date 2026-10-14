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

Also worth asking, where the answer is not yet in the register: Hong Kong (e-Legislation), Norway (Lovdata's public
texts), Belgium (Belgisch Staatsblad / Justel), Lithuania (e-TAR), the Philippines (Official Gazette / Lawphil), Mexico
(Diputados) and Poland (ISAP's API). Research each publisher's current terms first; the site may already offer an open
data route that makes the request unnecessary.

## When a reply arrives

1. Save the message text and date in the source's `licence.notes` (never paste personal data).
2. If they grant access: set `status: active`, write the manifest and connector as for any source
   ([onboarding-a-jurisdiction.md](onboarding-a-jurisdiction.md)), keep the crawl delay they ask for.
3. If they refuse or do not answer: leave the source `planned`. Do not use mirrors of unclear licence.
