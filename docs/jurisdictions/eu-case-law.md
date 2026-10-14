# European Union - case law (Court of Justice and General Court)

Status: **pilot, part of the draft_unverified EU profile (0.2.0)**. 31 landmark judgments in English and French. Not reviewed by counsel. Legislation: `eu.md`.

## Source, access, licence (reviewed 2026-10-14)

Source `eu-cellar-case-law` (`config/sources/eu-cellar-case-law.yaml`, manifest `config/manifests/eu-cellar-case-law.yaml`,
connector and parser `eu_cellar_case_law`). The files come from Cellar by CELEX number, like the legislation: per judgment two
SPARQL queries (work metadata; per language case designation, parties, title, XHTML files) and one XHTML file per language,
through `PoliteHttp` (5 s between requests, cache). **curia.europa.eu and eur-lex.europa.eu were not used** (bot challenges).
`publications.europa.eu/robots.txt` redirects to `op.europa.eu/robots.txt`: `User-agent: *`, `Allow: /`, no Crawl-delay,
`/resource/` and `/webapi/` not disallowed. About 130 requests in all (124 responses cached).

Licence: **reuse with acknowledgement** (`approved`, `allowed_with_conditions`). The Cellar dataset
<https://data.europa.eu/data/datasets/sparql-cellar-of-the-publications-office> gives every distribution the Commission reuse
notice <http://data.europa.eu/eli/dec/2011/833/oj>. Decision 2011/833/EU (read again from Cellar): reuse for any purpose, free,
without application (art 4); acknowledge the source, do not distort, no liability (art 6(2)); third-party rights excluded
(art 2(2)). **Caveat:** its arts 1-2 cover documents held by or produced for the Commission, so a judgment is covered only through
the Publications Office's statement for Cellar. The Court's copyright page <https://curia.europa.eu/site/jcms/d2_5167/en/copyright>
(case law may be reproduced with acknowledgement) was read in the legislation review but **not re-read**; a title search in Cellar
for a Court decision on reuse found none. Counsel review before production use. Attribution: "Source: Court of Justice of the
European Union, via EUR-Lex / Cellar, Publications Office of the European Union. © European Union."

## What is ingested
Data protection: Schrems (C-362/14), Schrems II (C-311/18), Google Spain (C-131/12), Digital Rights Ireland (C-293/12). Primacy and
references: Van Gend en Loos (26/62), Costa (6/64), Simmenthal (106/77), Internationale Handelsgesellschaft (11/70), Factortame
(C-213/89), CILFIT (283/81). State liability: Francovich (C-6/90), Brasserie du Pêcheur (C-46/93), Köbler (C-224/01). Fundamental
rights: Kadi (C-402/05 P; General Court T-315/01), Mangold (C-144/04), Defrenne II (43/75), Åkerberg Fransson (C-617/10), ASJP
(C-64/16), Wightman (C-621/18). Free movement: Bosman (C-415/93), Cassis de Dijon (120/78), Dassonville (8/74), Keck (C-267/91).
Competition: Consten and Grundig (56/64), Hoffmann-La Roche (85/76), United Brands (27/76), Courage (C-453/99), Intel (C-413/14 P),
Google Shopping (General Court T-612/17). Consumer: Aziz (C-415/11). 29 Court of Justice, 2 General Court.

Per language version: court (`EU-CJEU`, `EU-GC`), decision date, case designation and parties (Cellar metadata), ECLI as neutral
citation, work URI `/eu/case/ECLI:EU:C:2015:650`, every text block in order; no summaries, translations or headnotes by ULIS. A
judgment has one version per language (`version_date` = decision date). `authoritative` is true only for the language of the case
when Cellar records it (to verify: the other versions are the Court's translations); Cellar has none for Brasserie du Pêcheur and
the Kadi appeal.

## Structure and text
Two XHTML layouts are parsed. **Converted from Formex** (recent): numbered points as table rows, the operative part as an item table,
ECLI links to cited judgments. **Digitised Reports** (older): one block per `<p>`, often in capitals, point numbers part of the text;
the 1960s judgments have none. There the point numbers are inferred as the longest run of leading numbers increasing by one (two where
the source lost one), so quoted provisions and question lists are not taken for points. `num` is the Court's point number (`para_94`);
blocks without one (headings, quotations in older layouts) keep their order as `u_K`, plus `header`, `index`, `subject`; the operative
part ("On those grounds ... rules:") is `decision` paragraphs `op_N`, `num` `op-N`. Whitespace runs collapse; U+2011 is written `-`.

## Authority, precedent, citations

Profile 0.2.0: `jurisprudence_constante`, `vertical_binding: false`, notes (to verify) on settled case law, TFEU art 267 and appeals
from the General Court. `classify_judgment`: for a member State (per profile, on the date) a CJEU/GC decision is `regional_case_law`
("Case law of the court of a regional organisation of which the State is a member", weight 1.05); searching `EU` gives the new
`organisation_case_law` (before: "persuasive only"); non-members `foreign`. The rationale quotes the profile and the hit carries an
`AUTHORITY` limitation. Effects inside each Member State are not modelled. Citations (module `eu` 1.1.0): `Case C-362/14 Schrems
(EU:C:2015:650), para 94`; `Affaire C-362/14 Schrems (EU:C:2015:650), point 94`; `..., operative part, point 2` / `..., dispositif,
point 2`; `Joined Cases C-293/12 and C-594/12 Digital Rights Ireland and Others (EU:C:2014:238), para 39`. The designation is Cellar's per
language; the label (`short_name`) is the one the Court uses for the case in other ingested judgments (22 of 31 found), else the parties.

## References

A link the source marks to another judgment (ECLI, with the point cited) is a `cites` link (`source_markup`), resolved when that
judgment is ingested, otherwise unresolved. Articles of the ingested legislation are linked (`text_pattern`, to the article) only for
`Article N TFEU/TEU`, `Article N of the Charter` (short form only if no other charter is named) and `of Regulation (EU) 2016/679` and
the other ingested acts, in English and French. Not linked: OJ references, pre-Lisbon numbering, ranges.
Known gaps: 31 judgments only; no Opinions of Advocates General, orders or headnotes; headings and quotations stay unnumbered blocks in older
layouts; inferred numbering may miss a lost number; no later treatment; authenticity of the versions to verify; 2 of 24 languages.

## Results (2026-10-14)

Dev database `ulis_cjeu`: `ingest eu-eur-lex` (24 documents), then `ingest eu-cellar-case-law`: `documents_seen 62, judgments_created
31, paragraphs_created 9661, citation_links_created 1764 (resolved 854), passages_indexed 9661, errors 0`; a second run: 62 unchanged.
Links: 1,046 `source_markup` (136 to ingested judgments) and 718 `text_pattern` (all to a provision of the ingested TFEU, TEU, Charter or
GDPR). `verify-sources`: 86 of 86 pass. Searches: "transfer of personal data to a third country adequate level of protection essentially
equivalent" -> `Case C-362/14 Schrems (EU:C:2015:650), para 73` (`EU`: organisation_case_law; `PL`: regional_case_law); "national court
must apply Community law in its entirety" (`DE`) -> `Case 106/77 Simmenthal (EU:C:1978:49), para 21`; "exploitant d'un moteur de
recherche" -> `Affaire C-131/12 Google Spain and Google (EU:C:2014:317), point 17`. `ruff`, `mypy` clean; full suite 529 passed (27 in
`tests/test_eu_cellar_case_law.py`, on five real fixtures of 460 KB and the Charter fixture).
