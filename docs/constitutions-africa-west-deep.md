# Constitutions: North, West and Central Africa (deep sweep)

Outcome of the deep sweep for 30 states (Algeria, Egypt, Libya, Morocco, Tunisia, Sudan, Mauritania, Senegal, Gambia, Guinea, Guinea-Bissau, Sierra Leone, Liberia, Cote d'Ivoire, Ghana, Togo, Benin, Nigeria, Niger, Mali, Burkina Faso, Cabo Verde, Cameroon, Chad, Central African Republic, Gabon, Congo, DR Congo, Equatorial Guinea, Sao Tome and Principe), read on 2026-10-15/16. Method: a web search (which touches no target site) only to discover candidate official URLs; every request to a target site went through `PoliteHttp` (identifying User-Agent `ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)`, robots.txt of the host read first and the path checked against it, at least 5 s between requests, the Crawl-delay when longer); the publisher's terms were read before any text was stored. A refusal, a TLS verification failure, a connection reset or a prohibition ended the work on that publisher; nothing was worked around. Research record, not legal advice.

Shared machinery: connector `constitution_docs`, parser `constitution_text` (the sweep of Africa, the Middle East, Asia and Oceania, docs/constitutions-africa-asia-oceania.md); one source descriptor and one manifest per publisher, one minimal `draft_unverified` profile per country, citation module `constitution` (`<Country> Constitution art. N`). Two backward-compatible additions to the parser for this sweep: headings written `Article 1er` / `1ère` are read as article 1 (French), and the structure hint `columns: N` reads a PDF printed in N columns (an official gazette) strip by strip so that the columns do not interleave.

Licence reading: AMBER (active; `commercial_use: unknown`, so a commercial deployment is refused by the gate) where the publisher states no restriction beyond a generic footer and the text is clearly the official one; RED (rejected or planned, refused by the gate, `tests/test_constitution_west_africa.py`) where a notice forbids reproduction or access was refused.

## Result

Loaded: 17 publishers, 17 countries, 18 documents (counts of provisions are in the next section). Not loaded: 16 publishers examined and refused, unreadable or unreachable, and 2 countries with no official host found.

### The 30 states

Loaded now (17): Benin, Cote d'Ivoire, Mauritania, Cabo Verde, Algeria, Morocco, Niger, Gabon, Burkina Faso, Togo, Guinea-Bissau, Democratic Republic of the Congo, Cameroon, Mali, Congo, Equatorial Guinea, Liberia. Loaded by the earlier sweep (1): Senegal.

Still without a constitution (12), with the main reason:

* Egypt: the Presidency's PDF of the Constitution (April 2019 edition) has an Arabic text layer with every lam-alef ligature in reverse order; sis.gov.eg fails TLS verification and www.egypt.gov.eg resets the connection.
* Libya: the only official PDF found has a damaged Arabic text layer; no consolidated text of the Constitutional Declaration with its amendments.
* Tunisia: the Prime Ministry's PDF of the JORT issue has a corrupt Arabic text layer; legislation.tn resets the connection.
* Sudan: no official page with the Constitutional Document found.
* Gambia: the National Assembly's terms of use reserve all rights, allow personal non-commercial use only and forbid collecting data without permission.
* Guinea: the Supreme Court's PDF has no text layer (scan); the Government's Journal officiel site forbids reproduction in its legal notice.
* Sierra Leone: the Parliament's host fails TLS verification (self-signed certificate); the Electoral Commission prints only the sections about the Commission.
* Ghana: the Judicial Service's chapter pages lose the article numbers; the PDFs on other official hosts are OCR with errors, misnumbered, or a commercial compilation.
* Nigeria: the Ministry of Justice's PDF has no text layer; no readable official text found.
* Chad: robots.txt of the Presidency answers HTTP 403; the Constitutional Council's site is unreachable through the proxy.
* Central African Republic: no official page or PDF with the Constitution found.
* Sao Tome and Principe: the Ministry of Justice's legislation portal is unreachable through the proxy (HTTP 502 tunnel error).

Other laws and codes: the sweep went after the constitutions. Where a loaded publisher also showed codes, they were looked at: the Présidence du Faso's page offers a PDF titled 'CODE PENAL DE 1996' (83 pages), a text the Burkina Faso penal code of 2018 replaced, and an electoral-code PDF of 44 articles that is a modifying law; neither was loaded. The Moroccan Ministry of Justice's Adala portal (adala.justice.gov.ma) publishes the legislation of Morocco, and the Journal officiel sites of Algeria (joradp.dz) and Togo (jo.gouv.tg) publish every law, but they reset connections or are issue-by-issue PDF archives; they are not in this sweep.

### Loaded

| Country | Source | Publisher | Languages | Documents | Text |
|---|---|---|---|---|---|
| Benin | `bj-courconst-constitution` | Cour constitutionnelle du Bénin (courconstitutionnelle.bj) | fr | 1 | Preamble and 160 articles (with the articles numbered 157-1 to 157-3) as updated by the loi n° 2019-40 of 7 November 2019, as the Court's PDF prints them. |
| Cote d'Ivoire | `ci-cc-constitution` | Conseil constitutionnel de Côte d'Ivoire (conseil-constitutionnel.ci) | fr | 1 | Preamble and 184 articles of the Constitution of 8 November 2016 as the Council's PDF prints them. |
| Mauritania | `mr-primature-constitution` | Premier Ministère, Secrétariat Général du Gouvernement, Direction Générale de la Législation, de la Traduction et de l'Edition du Journal Officiel (primature.gov.mr) | fr | 1 | Preamble and the articles of the Constitution as consolidated by the Government up to the laws of 15 August 2017. |
| Cabo Verde | `cv-parlamento-constitution` | Assembleia Nacional de Cabo Verde (parlamento.cv) | pt | 1 | Preamble and 295 articles (one heading not recognised) of the Constitution as revised in 2010, as the Assembly's page prints them. |
| Algeria | `dz-joradp-constitution` | Secrétariat Général du Gouvernement, Journal officiel de la République algérienne (joradp.dz) | fr | 1 | Preamble and 225 articles of the Constitution as promulgated in the Journal officiel n° 82 of 30 December 2020 (French edition), with the translated oaths as a trailing block. |
| Morocco | `ma-adala-constitution` | Ministère de la Justice du Royaume du Maroc (portail Adala, adala.justice.gov.ma) | fr | 1 | Preamble and 180 articles of the Constitution of 2011 as the Ministry of Justice's PDF prints them. |
| Niger | `ne-gouv-constitution` | Portail officiel du Gouvernement du Niger (gouv.ne) | fr | 1 | Preamble and 185 articles of the Constitution of the 7th Republic as the Government portal's PDF prints them. |
| Gabon | `ga-gouvernement-constitution` | Communication Gouvernementale / Secrétariat Général du Gouvernement du Gabon (gouvernement.ga) | fr | 1 | Preamble and 173 articles of the Constitution of 19 December 2024 as the Government portal's PDF of the Journal officiel prints them. |
| Burkina Faso | `bf-presidence-constitution` | Présidence du Faso (presidencedufaso.bf) | fr | 1 | Preamble and 173 articles of the Constitution of 1991 as amended up to November 2015, as the Présidence du Faso's PDF prints them. |
| Togo | `tg-jo-constitution` | Journal officiel de la République togolaise (jo.gouv.tg) | fr | 1 | Preamble and 100 articles of the Constitution promulgated on 6 May 2024 as the Journal officiel's special issue prints them. |
| Guinea-Bissau | `gw-anp-constitution` | Assembleia Nacional Popular da Guiné-Bissau (anp.gw) | pt | 1 | Preamble and 133 articles of the Constitution as the National People's Assembly's page prints them. |
| Democratic Republic of the Congo | `cd-medd-constitution` | Ministère de l'Environnement et Développement Durable, RDC (medd.gouv.cd) | fr | 1 | Preamble, 229 articles of the Constitution of 18 February 2006 as coordinated after the law of 20 January 2011 (Journal officiel special issue of 5 February 2011), and the Exposé des motifs as blocks. |
| Cameroon | `cm-assnat-constitution` | Assemblée nationale du Cameroun (assnat.cm) | fr, en | 2 | Preamble and 69 articles of the Constitution of 1996, in French (as modified and completed by the law of 14 April 2008, per its heading) and in English, as the National Assembly's PDFs print them. |
| Mali | `ml-koulouba-constitution` | Présidence de la République du Mali (koulouba.ml) | fr | 1 | Preamble and 191 articles of the Constitution promulgated on 22 July 2023 as the Presidency's PDF of the Journal officiel prints them. |
| Congo | `cg-gouvernement-constitution` | Gouvernement de la République du Congo (gouvernement.cg) | fr | 1 | Articles 1 to 246 of the Constitution of 25 October 2015 as the Government portal's PDF of the Journal officiel prints them. |
| Equatorial Guinea | `gq-gobierno-constitution` | Página Oficial del Gobierno de la República de Guinea Ecuatorial (guineaecuatorialpress.com) | es | 1 | Preamble and 134 articles of the Ley Fundamental as promulgated on 16 February 2012, as the Government's PDF prints them. |
| Liberia | `lr-mod-constitution` | Ministry of National Defense of Liberia (mod.gov.lr) | en | 1 | Preamble and 97 articles of the Constitution of Liberia as the Ministry of National Defense's PDF prints them (one heading not recognised). |

### Numbers

| Source | Documents | Stored provisions |
|---|---|---|
| `bj-courconst-constitution` | 1 | 179 |
| `ci-cc-constitution` | 1 | 185 |
| `mr-primature-constitution` | 1 | 102 |
| `cv-parlamento-constitution` | 1 | 295 |
| `dz-joradp-constitution` | 1 | 227 |
| `ma-adala-constitution` | 1 | 181 |
| `ne-gouv-constitution` | 1 | 186 |
| `ga-gouvernement-constitution` | 1 | 174 |
| `bf-presidence-constitution` | 1 | 179 |
| `tg-jo-constitution` | 1 | 106 |
| `gw-anp-constitution` | 1 | 134 |
| `cd-medd-constitution` | 1 | 240 |
| `cm-assnat-constitution` | 2 | 481 |
| `ml-koulouba-constitution` | 1 | 192 |
| `cg-gouvernement-constitution` | 1 | 246 |
| `gq-gobierno-constitution` | 1 | 135 |
| `lr-mod-constitution` | 1 | 104 |
| **Total** | **18** | **3346** |

Dev database run on 2026-10-16 (`migrate`, `sync-catalog`, `load-all --jobs 4` of the 17 sources): 17 sources ok, 18 documents, 0 errors, 0 conflicts, 3,346 stored provisions (articles, their numbered paragraphs, preambles, blocks); `verify-sources`: 18 of 18 documents pass (every stored text re-derived from its stored raw bytes).
Searches through `ulis.cli search` on that database (first hit, citation): DZ 'La capitale de la République est Alger' -> `Algeria Constitution art. 5`; MA 'Le Maroc est une monarchie constitutionnelle démocratique parlementaire et sociale' -> `Morocco Constitution art. 1`; ML 'Tous les Maliens naissent et demeurent libres et égaux en droits' -> `Mali Constitution art. 1`; CV 'A República de Cabo Verde reconhece a igualdade de todos os cidadãos perante a lei' -> `Cabo Verde Constitution art. 1`; TG 'La République togolaise est un Etat de droit laïc démocratique et social' -> `Togo Constitution art. 1`; CM 'The Republic of Cameroon shall be a decentralized unitary State' -> `Cameroon Constitution art. 1(2)`. Each hit carries the limitations PROFILE_UNVERIFIED, ATTESTED_ONLY_AT and NOT_AUTHENTIC_TEXT.
Tests: the full suite passes against the test database (1,596 passed, 12 min) with `ruff check`, `ruff format --check` and `mypy ulis` clean; `tests/test_constitution_west_africa.py` adds 44 tests (committed sources, manifests and profiles; the 16 refused or unreadable publishers are refused by the licence gate and quote the words that stopped the work; the parser additions; real fixtures per publisher; ingestion of four fixtures through the pipeline, search with citation and limitations, lookup, idempotent re-ingest and `verify-sources`). Fixtures: `tests/fixtures/constitution_west_africa/` (about 90 KB).
Network: 2 to 3 requests per source (robots.txt and the document), at least 5 s apart (10 s where the host's robots.txt sets a Crawl-delay: conseil-constitutionnel.ci, presidencedufaso.bf, jo.gouv.tg). Morocco's host (adala.justice.gov.ma) reset the connection on robots.txt during the load although it had answered during the discovery run; the stored bytes of that discovery run (the exact PDF received on 2026-10-15, with its retrieval time) were put in the connector's cache and ingested from there.

### Not loaded

| Country | Source | Outcome | Finding |
|---|---|---|---|
| Congo | `cg-sgg-constitution` (Secrétariat Général du Gouvernement de la République du Congo (sgg.cg)) | rejected | www.sgg.cg/robots.txt - "User-agent: *" and a Sitemap line only (nothing disallowed). The page "Mentions légales" (https://www.sgg.cg/fr/mentions-legales.html) says: "Ce site internet est édité par le Secrétariat Général du Gouvernement de la République du Congo ... Toute représentation totale ou partielle de ce site ou de son contenu (structure générale, textes, sons, logos, images animées ou non), par quelques procédés que ce soient, sans autorisation préalable et expresse du Secrétariat Général du Gouvernement de la République du Congo est interdite et constituerait une contrefaçon sanctionnée par le code de la Propriété Intellectuelle." The page "Constitution de la République du Congo" holds the text with the amendment of 7 January 2022 (JO 2022-1 spécial). |
| Congo | `cg-presidence-constitution` (Présidence de la République du Congo (presidence.cg)) | rejected | presidence.cg/robots.txt - "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php". The page "Mentions légales" (https://presidence.cg/mentions-legales/), section "5 - Propriété intellectuelle et contrefaçons", says: "https://presidence.cg/ est propriétaire des droits de propriété intellectuelle et détient les droits d'usage sur tous les éléments accessibles sur le site internet, notamment les textes ... Toute reproduction, représentation, modification, publication ..." and "Toute exploitation non autorisée du site ou de l'un quelconque des éléments qu'il contient sera considérée comme constitutive d'une contrefaçon et poursuivie." The page offers the PDF "Republique-du-Congo-Constitution-2015.pdf" (499 Ko). |
| Mali | `ml-sgg-constitution` (Secrétariat Général du Gouvernement du Mali (sgg-mali.ml)) | rejected | sgg-mali.ml/robots.txt - "User-agent: *" and a Sitemap line only (nothing disallowed). The page "Mentions légales" (https://sgg-mali.ml/fr/mentions-legales.html) says: "Ce site internet est édité par le Secrétariat Général du Gouvernement du Mali ... Toute représentation totale ou partielle de ce site ou de son contenu (structure générale, textes, sons, logos, images animées ou non), par quelques procédés que ce soient, sans autorisation préalable et expresse du Secrétariat Général du Gouvernement du Mali est interdite et constituerait une contrefaçon sanctionnée par le code de la Propriété Intellectuelle." The site publishes the Journal officiel by year (https://sgg-mali.ml/JO/2023/). Seven issues of 2023 (n° 17-2, 19, 20, 21, 22, 23, 26) were downloaded while looking for the issue with the Constitution, BEFORE this notice was read; they are not stored in the repository and not used. The Constitution is loaded from the Presidency (ml-koulouba-constitution). |
| Guinea | `gn-sgg-jo-constitution` (Secrétariat Général du Gouvernement de la République de Guinée, Journal officiel (journal-officiel.sgg.gov.gn)) | rejected | journal-officiel.sgg.gov.gn/robots.txt - "User-agent: *" "Allow: /". The page "Mentions légales" (https://journal-officiel.sgg.gov.gn/fr/mentions-legales.html) says: "Le site internet du Journal officiel est édité par le Secrétariat Général du Gouvernement de la République de Guinée. Toute représentation totale ou partielle de ce site ou de son contenu (structure générale, textes, sons, logos, images animées ou non), par quelques procédés que ce soient, sans autorisation préalable et expresse du Secrétariat Général du Gouvernement est interdite et constituerait une contrefaçon sanctionnée par le code de la Propriété Intellectuelle." The page "Constitution" of the site was requested in the same call as the legal notice (before the notice was read); it is not stored and not used. |
| Gambia | `gm-assembly-constitution` (National Assembly of The Gambia (assembly.gm)) | rejected | assembly.gm/robots.txt - "User-agent: *" "Disallow:" (nothing disallowed). The "Terms of Use" (https://assembly.gm/terms-of-use, last updated October 15, 2026) say: "Unless otherwise stated, the National Assembly of The Gambia owns the intellectual property rights for all material on this website. All intellectual property rights are reserved." They allow only to "View and print pages for personal, non-commercial use" and "Download public documents for informational purposes", and forbid to "Collect data without permission". The home page links the PDF "The 1997 Constitution" from a storage host (national-assembly.ams3.digitaloceanspaces.com); neither the PDF nor that host was requested. |
| Chad | `td-presidence-constitution` (Présidence de la République du Tchad (presidence.td)) | planned | presidence.td/robots.txt answered HTTP 403 to the ULIS bot. robots.txt (https://presidence.td/robots.txt) answered HTTP 403 to the ULIS bot: the host refuses automated clients; nothing further was requested and no workaround was tried. A search engine lists the PDF of the Constitution at https://presidence.td/wp-content/uploads/2024/06/Constitution.pdf. The Constitutional Council's site (conseil-constitutionnel.td) and the National Assembly's site (www.assemblee-nationale.td) could not be reached through the proxy ('Tunnel connection failed: 502 Bad Gateway'). |
| Egypt | `eg-sis-constitution` (State Information Service of Egypt (sis.gov.eg)) | planned | sis.gov.eg/robots.txt - TLS certificate verification failed (unable to get local issuer certificate). TLS verification of https://sis.gov.eg/robots.txt failed ('unable to get local issuer certificate'); verification is never disabled, so nothing was read. A search engine lists the English PDF 'Constitution of The Arab Republic of Egypt' at https://sis.gov.eg/media/67726/dustor-eng.pdf. An earlier sweep also found the host unreachable. The Government Services Portal (www.egypt.gov.eg, which lists the Constitution in Arabic and, in part, in English) answered a connection reset to robots.txt, so nothing was requested there. The Supreme Constitutional Court's host (www.sccourt.gov.eg) failed the TLS handshake and the House of Representatives' host (www.parliament.gov.eg) failed certificate verification (unable to get local issuer certificate). |
| Egypt | `eg-presidency-constitution` (Presidency of the Arab Republic of Egypt (presidency.eg)) | planned | www.presidency.eg/robots.txt - "User-agent: *" "Disallow: /_rf4JJ2R5cnYY_3HrSJ1GhxQcghpy4lFLgVzFRKmbDk=.html" "Crawl-delay: 20" and "Allow: /" (the PDF is allowed). The page 'الدستور' (footer 'الحقوق جميعها محفوظة لرئاسة الجمهورية © ٢٠٢٠', i.e. all rights reserved to the Presidency, a generic footer) offers the PDF 'دستور-جمهورية-مصر-العربية-2019.pdf' (114 pages, 2 MB): the House of Representatives' General Secretariat edition of April 2019 of the Constitution 'معدّل طبقاً للاستفتاء على تعديل الدستور' of 20-22 April 2019. It was fetched; its Arabic text layer extracts every lam-alef ligature in reverse order and the page marks are mixed into the lines (for example 'الانتخابات' comes out as 'االنتخابات'), so the words are not the words of the text, and no article heading is recognised. ULIS does not repair a text layer. The same PDF is not stored. |
| Tunisia | `tn-legislation-constitution` (legislation.tn (Tunisian legislation portal)) | planned | www.legislation.tn/robots.txt - connection reset by peer (twice). robots.txt of www.legislation.tn could not be read (connection reset by peer, two attempts); nothing was requested. A search engine lists the Constitution of 2022 on that site article by article (about 142 pages). The Prime Ministry's PDF https://pm.gov.tn/sites/default/files/2023-03/constitution-2022.pdf (robots.txt of pm.gov.tn allows it; 41 pages, the JORT issue printed by the Imprimerie Officielle) was fetched and its Arabic text layer is corrupt (symbols instead of letters), so it cannot be stored. |
| Libya | `ly-sld-constitution` (sld.gov.ly (Libyan government site)) | planned | sld.gov.ly/robots.txt - "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php". The PDF of the Constitutional Declaration of 2011 (30 pages) has a damaged Arabic text layer: letters are missing from the words (for example 'ال ـي قادهـا ال ـع'), so the text cannot be stored exactly. Libya has no consolidated text of the Declaration with its 13 amendments on an official site that was found. |
| Nigeria | `ng-justice-constitution` (Federal Ministry of Justice of Nigeria (justice.gov.ng)) | planned | justice.gov.ng/robots.txt - "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php". The PDF 'Nigerian-Constitution.pdf' (672 KB) has no text layer (pdftotext returns no text; it is a scan or an image PDF); ULIS does no OCR. The National Assembly's site (nass.gov.ng) has no robots.txt (an HTML page answers) and no Constitution link was found on its home page. |
| Guinea | `gn-coursupreme-constitution` (Cour suprême de Guinée (coursupreme.org.gn)) | planned | coursupreme.org.gn/robots.txt - a long list of named crawlers (search engines, AI crawlers, archive.org_bot, Wget, curl, HTTrack ...) are disallowed ("Disallow: /"); "User-agent: *" "Allow: /" - the ULIS bot is not named, so it is allowed. The page 'CONSTITUTION DU 26 09 2025 DE LA REPUBLIQUE DE GUINEE' (footer '© Supreme Court of Guinea 2024. All rights reserved.') links the PDF 'CONSTITUTION-DU-26-09-2025-RG.pdf' (21.8 MB, 72 pages) which has no text layer (72 characters in all); ULIS does no OCR. The Government's Journal officiel site (gn-sgg-jo-constitution) forbids reproduction; robots.txt of www.sgg.gov.gn and of presidence.gov.gn answered HTTP 403 to the ULIS bot (nothing further requested there). |
| Ghana | `gh-judicial-constitution` (Judicial Service of Ghana (judicial.gov.gh)) | planned | judicial.gov.gh/robots.txt - "User-agent: *" with the Joomla rules (Disallow /administrator/, /cache/, /images/, /includes/ ...); the chapter pages are not disallowed. The Constitution is published chapter by chapter (Chapter 001 ...); the pages print the clauses '(1) The Sovereignty of Ghana ...' without the article numbers (the list numbers are lost in the HTML), and some clauses appear twice, so the articles cannot be recognised and the text cannot be stored faithfully. Other official copies were examined: ohcs.gov.gh PDF (Act 527 of 1996 with the Constitution, 110 pages) has an OCR text layer with errors ('THE FI VI:' HUNDRED AND TWENTY -SEVENTH'); audit.gov.gh PDF (a print of the audit service's web pages, 215 pages) has articles out of order after chapter 14; osp.gov.gh has two PDFs: one (Ghana_Constitution.pdf) reproduces a commercial compilation ('Constitutional documents available for the Republic of Ghana', with an editor's note) and is not an official text; the other (constitutionofghana.pdf, 110 pages) is the same OCR scan as the ohcs.gov.gh PDF. Footer of judicial.gov.gh: 'Copyright © 2026 Judicial Service of Ghana. All Rights Reserved'. |
| Sierra Leone | `sl-parliament-constitution` (Parliament of Sierra Leone (parliament.gov.sl); Electoral Commission (ec.gov.sl)) | planned | www.parliament.gov.sl/robots.txt - TLS certificate verification failed (self-signed certificate). TLS verification of https://www.parliament.gov.sl/robots.txt failed ('self-signed certificate'); verification is never disabled, so nothing was read there. The Electoral Commission (ec.gov.sl, Crawl-delay 30) prints only the sections of the 1991 Constitution that concern the Commission (about 7,300 characters), not the Constitution. The Judiciary (judiciary.gov.sl) offers 'Constitution of Sierra Leone Act No. 6 1991' as a download from its Rules and Procedures page; that file was not located. |
| Democratic Republic of the Congo | `cd-presidence-constitution` (Présidence de la République démocratique du Congo (presidence.cd)) | planned | presidence.cd/robots.txt - "User-agent: *" "Disallow:" (nothing disallowed). The "Mentions légales" (https://presidence.cd/mentions_legales), section "Droits d'auteurs", say: "Les documents 'publics' ou 'officiels' peuvent donc être reproduits librement ... Les informations utilisées ne doivent l'être qu'à des fins personnelles, associatives ou professionnelles. Toute utilisation à des fins commerciales ou publicitaires est formellement interdite." with the conditions "gratuité de la diffusion ; respect de l'intégrité des documents reproduits ; citation explicite du site presidence.cd comme source". That is a non-commercial permission, but the Presidency's PDF of the Constitution ("Constitution de la 3me Republique. 18 Fev 2006.pdf", 36 pages) is a page of the printed compilation "LES CONSTITUTIONS DE LA RDC DE 1908 À 2011" in two columns whose text layer is out of reading order (article 2 before the heading of section 1), so it is not stored; the same text is loaded from the Ministry of the Environment (cd-medd-constitution). |
| Sao Tome and Principe | `st-justica-constitution` (Ministry of Justice of Sao Tome and Principe, legislation portal (justica.gov.st)) | planned | justica.gov.st/robots.txt - the proxy answered 'Tunnel connection failed: 502 Bad Gateway' (two attempts, one day apart). robots.txt of justica.gov.st could not be read (the proxy answered 'Tunnel connection failed: 502 Bad Gateway' twice, on 2026-10-15 and 2026-10-16), so nothing was requested from the host. A search engine lists its legislation page as holding the Constitution among its categories; the Diário da República (dre.gov.st) and the Assembleia Nacional (anp-stp.gov.st) were found by search only and not requested. |
| Sudan | - | not found | Searches (restricted to moj.gov.sd, sudan.gov.sd, parliament.gov.sd and other official domains) returned no official page with the Constitutional Document of 2019 (as amended in 2020 and 2025). The Government portal (sudan.gov.sd; robots.txt only names a sitemap) was read: its home page links no constitution; the Ministry of Justice's site (moj.gov.sd; no robots.txt) answers an empty page. |
| Central African Republic | - | not found | www.gouv.cf (robots.txt: Crawl-delay 10) only describes the country ('La République Centrafricaine'); no official page or PDF with the Constitution of 30 August 2023 (or of 2016) was found there. A search engine lists, on the Embassy in Paris' site (paris.diplomatie.gouv.cf, 'Textes officiels'), a PDF 'Nouvelle-Constitution_RCA_Bonne-Version_2023.pdf' labelled 'Projet de la nouvelle Constitution centrafricaine'; robots.txt of that host answered 'connection reset by peer', so nothing was requested there. |
| Senegal | - | not found | Already loaded by the earlier sweep (sn-cc-constitution); not repeated. |

## Findings per loaded publisher

The text below is the header of each source descriptor: the robots.txt, the terms and the document as read.

### Benin - `bj-courconst-constitution`

```text
robots.txt - courconstitutionnelle.bj/robots.txt - "User-agent: *" "Disallow:" (nothing disallowed); no Crawl-delay
Terms - no terms-of-use, licence or copyright page was found on the pages read (home page and its footer "© Cour Constitutionnelle du Bénin - 2023")
The document - The Court publishes a PDF of the Constitution headed "Loi n° 90-32 du 11 décembre 1990 (à jour de sa révision par la loi N° 2019-40 du 07 Novembre 2019 portant révision de la loi n°90-32 du 11 décembre 1990 portant Constitution de la République du Bénin)" (35 pages, text layer): preamble and articles 1 to 160 with the articles added by the revision (157-1 ...).
```

### Cote d'Ivoire - `ci-cc-constitution`

```text
robots.txt - www.conseil-constitutionnel.ci/robots.txt - "User-agent: *" "Crawl-delay: 10", then Disallow lines for the CMS directories (/includes/, /misc/, /modules/, /profiles/, /scripts/, /themes/, /admin/, /search/, /user/ ... ); /sites/default/files/ is not disallowed. The connector waits 10 s between requests
Terms - no terms-of-use, licence or copyright page was found on the pages read (home page, menu, "La Constitution" page)
The document - The page "La Constitution" (https://www.conseil-constitutionnel.ci/lois-et-decrets/la-constitution) offers "La troisième constitution" for download: a PDF headed "LA TROISIEME CONSTITUTION DU 8 NOVEMBRE 2016" (41 pages, text layer): preamble and articles 1 to 184. The PDF carries no amendments (the constitutional law n° 2020-348 of 19 March 2020 is not applied).
```

### Mauritania - `mr-primature-constitution`

```text
robots.txt - www.primature.gov.mr/robots.txt - "User-agent: *" with the standard Drupal rules (Disallow /core/, /profiles/, /admin/, /search/, /user/ ... ); /sites/default/files/ is not disallowed; no Crawl-delay
Terms - no terms-of-use, licence or copyright page was found on the pages read (home page); the PDF is headed by the Government's own legislation and Journal officiel directorate
The document - The PDF "Constitution_consolidefr.pdf" (25 pages, text layer) is headed "République Islamique de Mauritanie / Premier Ministère / Secrétariat Général du Gouvernement / Direction Générale de la Législation, de la Traduction et de l'Edition du Journal Officiel" and "Constitution de la République Islamique de Mauritanie promulguée par l'ordonnance n° 91 - 022 du 20 juillet 1991, et révisée par" the constitutional laws n° 2006-014 (12 July 2006), n° 2012-015 (20 March 2012) and the two referendum laws n° 2017-021 and 2017-022 (15 August 2017). The text keeps "(nouveau)" after the articles the revisions replaced.
```

### Cabo Verde - `cv-parlamento-constitution`

```text
robots.txt - www.parlamento.cv/robots.txt - HTTP 404 (no robots.txt): no crawling rules to apply. The connector reads it anyway
Terms - page footer: "© Assembleia Nacional. Todos os Direitos Reservados." - a generic footer; no terms-of-use or licence page was found. The page itself says "Este texto não substitui o original publicado no Boletim Oficial."
The document - The page "Constituição da República" (https://www.parlamento.cv/Constituicao/) prints the whole text under the heading "CONSTITUIÇAO DA REPÚBLICA DE CABO VERDE / 2ª Revisão Ordinária - 2010": preamble and articles 1 to 295 in seven parts. The page prints "(Artigo 162º)" in brackets, which the parser does not read as a heading.
```

### Algeria - `dz-joradp-constitution`

```text
robots.txt - www.joradp.dz/robots.txt - HTTP 404 (no robots.txt): no crawling rules to apply. The connector reads it anyway. joradp.dz/ answers with a page that redirects to www.joradp.dz
Terms - no terms-of-use, licence or copyright page was found on the pages read (the portal's entry page, titled "SGG Algérie", has no footer); the file is the Imprimerie Officielle's Journal officiel
The document - The Journal officiel n° 82 of 30 December 2020 (French translation edition, 49 pages, text layer) prints the presidential decree n° 20-442 promulgating the constitutional revision "adoptée par référendum du 1er novembre 2020" and, as its annex, "CONSTITUTION DE LA REPUBLIQUE ALGERIENNE DEMOCRATIQUE ET POPULAIRE": preamble, articles 1 to 225 and, after them, the translated oaths of articles 90 and 186 (kept as a block).
```

### Morocco - `ma-adala-constitution`

```text
robots.txt - adala.justice.gov.ma/robots.txt - HTTP 404 (no robots.txt): no crawling rules to apply. The connector reads it anyway
Terms - page footer: "© 2026 وزارة العدل - المملكة المغربية" - a generic footer; no terms-of-use or licence page was found
The document - The PDF "costitution-1709118294788.pdf" (58 pages, text layer) opens with "Dahir n°1-11-91 du 27 chaabane 1432 (29 juillet 2011) portant promulgation du texte de la constitution" (Bulletin officiel n° 5964 bis du 28 chaabane 1432 (30-7-2011)) and prints "LA CONSTITUTION": preamble and articles 1 to 180.
```

### Niger - `ne-gouv-constitution`

```text
robots.txt - www.gouv.ne/robots.txt - "User-agent: *" with the Joomla rules (Disallow /administrator/, /bin/, /cache/, /components/, /includes/, /language/, /layouts/, /libraries/, /logs/, /modules/, /plugins/, /tmp/ ...); /images/ is not disallowed; no Crawl-delay
Terms - page footer: "Copyright © 2021 - 2024 – PORTAIL OFFICIEL DU GOUVERNEMENT DU NIGER ** Tous droits réservés | Conditions d'utilisation" - the "Conditions d'utilisation" are plain text, not a link, and no terms page was found
The document - The page "Constitution de la 7ème République" (https://www.gouv.ne/index.php/textes-fondamentaux/constitution-de-la-7eme-republique, section "Textes fondamentaux") links the PDF "constitution.pdf" (46 pages, text layer) headed "CONSTITUTION DE LA VIIème REPUBLIQUE (Promulguée par décret n°2010-754/PCSRD du 25 novembre 2010) JORN n° spécial 19 du 29 novembre 2010": preamble and articles 1 to 185.
```

### Gabon - `ga-gouvernement-constitution`

```text
robots.txt - gouvernement.ga/robots.txt - "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php"; no Crawl-delay; /wp-content/uploads/ is not disallowed
Terms - page footer: "© Communication Gouvernementale 2026 . All rights reserved." - a generic footer; no terms-of-use or licence page was found
The document - The page "Nouvelle constitution" (https://gouvernement.ga/nouvelle-constitution/, under the SGG menu) offers the PDF of the Journal officiel n° 47 Bis of 19 December 2024 (29 pages, text layer, two columns): "Loi référendaire n°002-R/2024 du 19 décembre 2024 portant Constitution de la République Gabonaise", preamble and articles 1 to 173.
```

### Burkina Faso - `bf-presidence-constitution`

```text
robots.txt - presidencedufaso.bf/robots.txt and www.presidencedufaso.bf/robots.txt - "User-agent: *" "Crawl-delay: 10" and nothing else (nothing disallowed). The connector waits 10 s between requests
Terms - page footer of "Textes fondamentaux": "© Présidence du Faso." - a generic footer; no terms-of-use or licence page was found
The document - The page "Textes fondamentaux" (https://www.presidencedufaso.bf/textes-fondamentaux/) says the Constitution "fut approuvée par référendum le 2 juin 1991, formellement adoptée le 11 juin 1991 et dernièrement amendée en novembre 2015. Elle est constituée d'un préambule et de 173 articles" and links the PDF "La-constitution-du-Burkina-Faso.pdf" (39 pages, text layer): an "AVANT-PROPOS" by the Presidency (not stored) and the text, preamble and articles 1 to 173 (with article 168-1). The PDF is hosted on the same site's upload folder (the page links it on presidencedufaso.net; the same path on presidencedufaso.bf answers).
```

### Togo - `tg-jo-constitution`

```text
robots.txt - jo.gouv.tg/robots.txt - "User-agent: *" "Crawl-delay: 10", then Disallow lines for the CMS directories (/includes/, /misc/, /modules/, /profiles/, /scripts/, /themes/ ... ); /sites/default/files/ is not disallowed. The connector waits 10 s between requests
Terms - page footer: "Copyright © 2026" - no owner named, no terms-of-use or licence page was found on the pages read (the entry page of the law and the "Lois" index)
The document - The page https://jo.gouv.tg/node/15758 is the entry "2024-005" of the Journal officiel's law index (Loi 2024-005 du 06/05/2024 portant Constitution de la République Togolaise) and links the PDF of the special issue "69e Année N° 42 Bis, 06 mai 2024, NUMERO SPECIAL, JOURNAL OFFICIEL DE LA REPUBLIQUE TOGOLAISE" (18 pages, text layer, two columns): the Constitution of the Fifth Republic, preamble and articles 1 to 100.
```

### Guinea-Bissau - `gw-anp-constitution`

```text
robots.txt - anp.gw/robots.txt - "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php"; no Crawl-delay; /index.php/constituicao/ is not disallowed
Terms - page footer: "© 2026 Assembleia Nacional Popular | Todos os Direitos Reservados" - a generic footer; no terms-of-use or licence page was found
The document - The page "Constituição de República da Guiné-Bissau" (https://anp.gw/index.php/constituicao/) prints the text: a preamble ("O PAIGC, fundado em 19 de Setembro de 1956 ...") and articles 1 to 133 in titles. The page shows no edition date or list of amendments.
```

### Democratic Republic of the Congo - `cd-medd-constitution`

```text
robots.txt - medd.gouv.cd/robots.txt - "User-agent: *" "Disallow:" (nothing disallowed) and a Sitemap line; no Crawl-delay
Terms - page footer: "Copyright © 1975 – 2025 MEDDNEC par la DANTIC. Tous droits réservés." - a generic footer; no terms-of-use or licence page was found. The Presidency's own site (presidence.cd) has a "Mentions légales" page that lets "documents publics ou officiels" be reproduced freely for personal, associative or professional uses (commercial use forbidden; integrity and citation of the site required), but its PDFs of the Constitution are a printed compilation whose text layer is out of reading order (see docs/constitutions-africa-west-deep.md)
The document - The Ministry's page "CONSTITUTION DE LA REPUBLIQUE DEMOCRATIQUE DU CONGO Modifiée par la Loi n° 11/002 du 20 janvier 2011 portant révision de certains articles de la Constitution de la République Démocratique du Congo du 18 février 2006 (Textes coordonnés)" embeds the PDF "DR-Congo-Constitution-Revised-2011.pdf" (86 pages, text layer): "Journal Officiel Numéro spécial 5 février 2011 – Constitution de la République Démocratique du Congo": preamble, titles I to VIII, articles 1 to 229, then the "Exposé des motifs" (kept as blocks).
```

### Cameroon - `cm-assnat-constitution`

```text
robots.txt - www.assnat.cm/robots.txt - "User-agent: *" with the Joomla rules (Disallow /administrator/, /bin/, /cache/, /components/, /includes/, /installation/, /language/, /layouts/, /libraries/, /logs/, /modules/, /plugins/ ...); /images/ is not disallowed; no Crawl-delay
Terms - the home page has no footer text and no terms-of-use or licence page was found on the pages read
The document - The Assembly's site hosts two PDFs of the Constitution (30 pages each, text layer): https://www.assnat.cm/images/La_Constitution.pdf headed "Loi n°96/06 du 18 janvier 1996 Portant révision de la Constitution du 02 juin 1972, modifiée et complétée par la loi n°2008/001 du 14 avril 2008" and https://www.assnat.cm/images/The_constitution.pdf headed "Law No. 96-6 of 18 January 1996 to amend the Constitution of 2 June, 1972" (English): preamble and articles 1 to 69. The French text prints "ARICLE 48" (a misprint), which the keywords hint of the manifest reads. Both languages are official in Cameroon.
```

### Mali - `ml-koulouba-constitution`

```text
robots.txt - koulouba.ml/robots.txt - "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php"; no Crawl-delay; /wp-content/uploads/ is not disallowed
Terms - page footer: "©2026 – Présidence de la République du MALI" - a generic footer; no terms-of-use or licence page was found. (The Secrétariat Général du Gouvernement's site sgg-mali.ml has a "Mentions légales" page that forbids any reproduction without authorization; it is not used: see docs/constitutions-africa-west-deep.md)
The document - The page "La Constitution du 22 juillet 2023" (https://koulouba.ml/la-presidence/histoire-symboles/les-textes-fondateurs/constitution-version-actuelle/) says "Le texte est constitué d'un préambule suivi de 14 titres et de 191 articles" and links the PDF "Constitution-promulguee.pdf" (17 pages, text layer, two columns): the Journal officiel special issue n° 13 of 22 July 2023 with the decree n° 2023-0401/PT-RM promulgating the Constitution; preamble and articles 1 to 191.
```

### Congo - `cg-gouvernement-constitution`

```text
robots.txt - www.gouvernement.cg/robots.txt - "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php"; no Crawl-delay; /wp-content/uploads/ is not disallowed
Terms - page footer of the home page: "Politique de confidentialité | Conditions générales | Cookies | Contacts © 2022 République du Congo. Tous droits réservés." - "Conditions générales" is a link to "#" (no terms page); no licence was found. TWO OTHER Congolese publishers of the same Journal officiel text forbid reproduction in their legal notices and are recorded as rejected (cg-sgg-constitution, cg-presidence-constitution): the reading of this source is therefore the least secure of the AMBER ones
The document - The Government portal hosts the PDF "constitution_25_10_2015-1.pdf" (24 pages, text layer, two columns): the Journal officiel text "CONSTITUTION DE LA REPUBLIQUE DU CONGO ADOPTEE PAR REFERENDUM LE 25 OCTOBRE 2015": articles 1 to 246 (the PDF's preamble is not recognised as such and is not stored).
```

### Equatorial Guinea - `gq-gobierno-constitution`

```text
robots.txt - www.guineaecuatorialpress.com/robots.txt - "User-agent: *" "Disallow:" (nothing disallowed); no Crawl-delay
Terms - the home page answers with an empty script page (161 bytes) and shows no footer; no terms-of-use or licence page was found
The document - The site, which calls itself "Página Oficial del Gobierno de la República de Guinea Ecuatorial", hosts the PDF "Ley_Fundamental_Guinea_Ecuatorial.pdf" (39 pages, text layer): "Nuevo texto de la Constitución de Guinea Ecuatorial, promulgada oficialmente el 16 de febrero de 2012. Con los textos de la Reforma Constitucional aprobados en referéndum el 13 de noviembre de año 2011": preamble and articles 1 to 134.
```

### Liberia - `lr-mod-constitution`

```text
robots.txt - mod.gov.lr/robots.txt - "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php"; no Crawl-delay; /wp-content/uploads/ is not disallowed
Terms - page footer: "© 2020 Armed Forces of Liberia. All rights Reserved" - a generic footer; no terms-of-use or licence page was found. The Judiciary of Liberia's PDF of the Constitution (judiciary.gov.lr) is a scan without text layer and was not used
The document - The Ministry hosts the PDF "THE-1986-CONSTITUTION-OF-THE-REPUBLIC-OF-LIBERIA.pdf" (50 pages, text layer): "CONSTITUTION OF THE REPUBLIC OF LIBERIA" with a table of contents, preamble and articles 1 to 97. The PDF prints no edition date.
```

