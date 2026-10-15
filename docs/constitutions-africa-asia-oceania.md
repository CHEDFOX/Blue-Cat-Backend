# Constitutions: Africa, the Middle East, Asia and Oceania

Outcome of the constitutions sweep for these regions, read on 2026-10-15. The aim is the text of each country's current constitution from a source that lets automated clients in and whose own terms allow storing and showing the text. Every source was read first: robots.txt of the host, then the publisher's terms, copyright or legal notice, and only then the document. A refusal (HTTP 401/403/451, a challenge page, a login) or a prohibition ended the work on that publisher; nothing was worked around. This is a research record, not legal advice; counsel review is recommended before production use.

## Method and licence reading

* One shared connector (`ulis/sources/connectors/constitution_docs.py`) and parser (`ulis/sources/parsers/constitution_text.py`), one source descriptor and one small manifest per publisher (`config/sources/<id>.yaml`, `config/manifests/<id>.yaml`), one minimal `draft_unverified` profile per country, and the generic citation module `constitution` (`<Country> Constitution art. 12`, as the document numbers it).
* The manifest names the document URLs, the language, the dates the source states (adoption, amendments) and per-document structure hints. The connector keeps the exact bytes received (decoded from gzip when the server sends it), the parser re-derives the text from them; articles are split by the document's own headings, chapters and parts become the context heading of the articles below them, and text that cannot be assigned to an article (schedules, annexes, footnotes) is stored as blocks of paragraphs and said to be so.
* Validity: a constitution page or PDF gives the current text without the dates of its provisions, so every text is `attested_at_edition` (known in force on the edition date the publisher prints, else on the retrieval date); the amendments the source names are events.
* Licence reading. GREEN: the publisher grants reuse in words. AMBER: the publisher states no restriction on the official text and no permission either (a generic copyright footer, no terms page); the source is `active` with `commercial_use: unknown` (a commercial deployment is refused by the gate) and the descriptor says which statute is believed to exclude official texts, marked 'to verify'. Non-commercial terms in the publisher's words (China, South Africa, Hong Kong) set `commercial_use: not_allowed`. RED: a prohibition or a refusal; the source is `rejected` or `planned` and the gate refuses it (`tests/test_constitution_blocked.py`).

## Result

Loaded: 17 publishers, 17 countries, 25 documents, 7,563 provisions (dev database run: all ingested without errors, `verify-sources` 25/25 pass). Not loaded: see the second table. Countries and publishers not reached are listed as such; the sweep did not cover every country.

### Loaded

| Country | Source | Publisher | Languages | Documents | Provisions | Commercial use |
|---|---|---|---|---|---|---|
| Armenia | `am-concourt-constitution` | Constitutional Court of the Republic of Armenia (Սահմանադրական դատարան), concourt.am | en | 1 | 720 | unknown |
| Bangladesh | `bd-bdlaws-constitution` | Legislative and Parliamentary Affairs Division, Ministry of Law, Justice and Parliamentary Affairs of Bangladesh | bn, en | 2 | 419 | unknown |
| China | `cn-govcn-constitution` | 中国政府网 (www.gov.cn), the central government portal of the People's Republic of China | zh | 1 | 144 | not_allowed |
| Hong Kong SAR | `hk-cmab-basiclaw` | Constitutional and Mainland Affairs Bureau, Government of the Hong Kong Special Administrative Region (www.basiclaw.gov.hk) | en | 1 | 179 | not_allowed |
| Indonesia | `id-mpr-constitution` | Majelis Permusyawaratan Rakyat Republik Indonesia (People's Consultative Assembly), Sekretariat Jenderal MPR RI, www.mpr.go.id | id | 1 | 244 | unknown |
| Kenya | `ke-parliament-constitution` | Parliament of Kenya (www.parliament.go.ke) | en | 1 | 1,185 | unknown |
| Kyrgyzstan | `kg-shailoo-constitution` | Central Commission for Elections and Referenda of the Kyrgyz Republic (Кыргыз Республикасынын Шайлоо жана референдум өткөрүү боюнча борбордук комиссиясы), www.shailoo.gov.kg | ru | 1 | 493 | unknown |
| Lebanon | `lb-presidency-constitution` | Presidency of the Lebanese Republic (رئاسة الجمهورية اللبنانية), www.presidency.gov.lb | ar | 1 | 97 | unknown |
| Macao SAR | `mo-dsaj-basiclaw` | Direcção dos Serviços de Assuntos de Justiça (DSAJ), Government of the Macao Special Administrative Region | en, pt, zh-Hant | 3 | 209 | unknown |
| Nepal | `np-lawcommission-constitution` | Nepal Law Commission (नेपाल कानून आयोग), lawcommission.gov.np | en | 1 | 340 | unknown |
| Pakistan | `pk-cci-constitution` | Council of Common Interests, Government of Pakistan (cci.gov.pk) | en | 1 | 661 | unknown |
| Senegal | `sn-cc-constitution` | Conseil constitutionnel du Sénégal (conseilconstitutionnel.sn) | fr | 1 | 109 | unknown |
| Sri Lanka | `lk-parliament-constitution` | Parliament of Sri Lanka (Parliament Secretariat; edited by the Bills Office of the Legislative Services Department), www.parliament.lk | en | 1 | 748 | unknown |
| Thailand | `th-senate-constitution` | Secretariat of the Senate of Thailand (สำนักงานเลขาธิการวุฒิสภา), www.senate.go.th | en | 1 | 592 | unknown |
| Uzbekistan | `uz-constitution-site` | Official national portal "Конституция Нового Узбекистана" (constitution.gov.uz, also konstitutsiya.gov.uz), Republic of Uzbekistan | ru, en, uz, uz-Cyrl | 4 | 156 | unknown |
| Vietnam | `vn-chinhphu-constitution` | Báo điện tử Chính phủ (Government Electronic Newspaper) of the Government Portal of Vietnam, xaydungchinhsach.chinhphu.vn | vi | 1 | 366 | unknown |
| South Africa | `za-doj-constitution` | Department of Justice and Constitutional Development of the Republic of South Africa (www.justice.gov.za) | en, af, zu | 3 | 901 | not_allowed |

### Not loaded

| Country | Source tried | Language | Outcome | Finding |
|---|---|---|---|---|
| Angola | Assembleia Nacional PDF (portaladmin.parlamento.ao) | pt | rejected | robots.txt allows everything, but the PDF's imprint says: 'Esta publicação, no seu todo ou em parte, não pode ser reproduzida ou transmitida por forma ou processo, mecânico ou fotográfico, incluindo fotocópia, sem autorização prévia da Assembleia Nacional.' Source `ao-parlamento-constitution`. |
| Bahrain | LLOC (lloc.gov.bh) | en | rejected | Terms and Conditions (25 April 2025): 'The contents of this website including information and software are protected by copyright and trademarks.' and 'use of the content for any other purpose is a violation'. Source `bh-lloc-constitution`. |
| Jordan | House of Representatives (representatives.jo) | ar | rejected | Copyright page: personal / internal non-commercial use and 'إعادة نشر أجزاء بسيطة من المعلومات' (republishing simple parts) only. Source `jo-representatives-constitution`. |
| Oman | decree.om (subscription database); mjla.gov.om | en, ar | rejected / unreachable | decree.om terms forbid copying, scraping, downloading and bots and say the content 'is not official'. The Ministry's own site (mjla.gov.om) did not answer (connection error on robots.txt). Source `om-decree-om`. |
| Iraq | Council of Representatives (iq.parliament.iq) | ar, en | blocked | robots.txt answered HTTP 403 to the bot. Source `iq-parliament-constitution`. The Ministry of Foreign Affairs page (mofa.gov.iq) quotes the text but was not evaluated further. |
| United Arab Emirates | uaelegislation.gov.ae; elaws.moj.gov.ae | ar, en | blocked | uaelegislation.gov.ae: robots.txt allows (`Disallow:` empty) but the page answered HTTP 403 to the bot; elaws.moj.gov.ae: robots.txt connection error. The legislation portal is the planned source `ae-uae-legislation`. |
| Qatar | almeezan.qa; gco.gov.qa | ar, en | blocked / unreadable | almeezan.qa is a script application (the page holds no text; planned source `qa-almeezan`); www.gco.gov.qa answered HTTP 403. |
| Kuwait, Saudi Arabia, Egypt | mona.gov.kw, gpf.gov.kw, shura.gov.sa, sis.gov.eg | ar, en | unreachable | robots.txt could not be fetched (connection error) on each host; nothing else was requested. |
| Iran, Syria, Yemen, Israel, Palestine | - | - | not reached | not examined in this run (Israel: knesset.gov.il has no robots.txt; the Basic Laws are separate documents). |
| Turkey, Azerbaijan, Georgia, Kazakhstan, Tajikistan, Turkmenistan | - | - | other sweep | covered by the Americas/Europe/Caucasus/Central Asia sweep (docs/constitutions-americas-europe.md), which records them as planned. The Turkish TBMM PDF parses completely with this sweep's parser (177 articles; the provisional articles between 174 and 175 via the `interlude_regex` hint), should that sweep's descriptor be activated. |
| India | legislative.gov.in, india.gov.in, main.sci.gov.in | en | blocked | robots.txt of legislative.gov.in and india.gov.in answered HTTP 403, main.sci.gov.in HTTP 502. PDFs of the Legislative Department on a CDN host (cdnbbsr.s3waas.gov.in) were found by a search engine; they were not used because the publisher's own host refused the bot. |
| Philippines | officialgazette.gov.ph, web.senate.gov.ph, sec.gov.ph, pcw.gov.ph, elibrary.judiciary.gov.ph | en | blocked | robots.txt answered HTTP 403 on the first four hosts and failed (connection error) on the Supreme Court e-library; lawphil.net (no robots.txt) is a private foundation, not an official source. |
| Singapore | sso.agc.gov.sg; eld.gov.sg; mlaw.gov.sg | en | blocked / terms not read | Singapore Statutes Online is a blocked source of the corpus (docs/corpus.md). The Elections Department PDF (www.eld.gov.sg; robots.txt without rules) was found but its terms page was not located, so it was not loaded. |
| Korea | ccourt.go.kr; elaw.klri.re.kr; law.go.kr | ko, en | no open source | ccourt.go.kr: robots.txt `Disallow: /`. The English text (KLRI translation on elaw.klri.re.kr and law.go.kr) carries 'Copyright (c) Korea Legislation Research Institute all rights reserved' and a copyright policy that, according to a search engine's excerpt of the Korean page, forbids unauthorised copying and distribution; the page returned no text to the bot. law.go.kr states the translation 'does not have any legal authority or official effect'. The Korean text on law.go.kr is script-rendered. |
| Mongolia | conscourt.gov.mn (Constitutional Court) | en | not loaded | The English page is a copy of the University of Bern ICL translation (its markup links to servat.unibe.ch/icl), whose copyright page is dead; the page also prints 'Article 6 [Economy]' where the Constitution has article 5. Footer '(c) 2024 Constitutional Court Secretariat'. |
| Malaysia, Bhutan, Zambia | parlimen.gov.my, nab.gov.bt, nationalcouncil.bt, parliament.gov.zm | en | unreachable | TLS certificate verification failed (hostname mismatch / unknown issuer) or the connection was reset; verification is never disabled. |
| Morocco | chambredesrepresentants.ma | ar, fr | blocked | A 'Just a moment...' challenge page; not worked around. |
| Nigeria | justice.gov.ng | en | unreadable | The PDF has no text layer. |
| Uganda | parliament.go.ug | en | outdated | The PDF is a 2007 print of the 1995 text (192 pages), not the current consolidated text; ULII is rejected (below). |
| Ghana | parliament.gh / judicial.gov.gh | en | outdated | The PDF is a 2009 print 'as amended to 1996'. |
| Tunisia | pm.gov.tn | ar | unreadable | The text layer of the 2022 Constitution PDF is out of reading order. |
| Botswana | parliament.gov.bw | en | blocked by notice | Every page of the PDF carries 'Copyright Government of Botswana'; no licence; the text layer is misordered. |
| Burkina Faso | conseil-constitutionnel.gov.bf | fr | not loaded | The footnote marks are fused to the article numbers ('Article 4317'); the structure cannot be recognised; robots.txt 404. |
| Algeria, Benin | joradp.dz, sgg.gouv.bj | fr | not examined | robots.txt allows (none / empty `Disallow:`); the constitution documents were not evaluated in this run. |
| New Zealand, Cambodia, Laos, Brunei and the rest of Africa and Oceania | - | - | not reached | Not examined in this run. |

### Multi-country publishers

| Publisher | Outcome | Finding |
|---|---|---|
| Constitute (constituteproject.org) | rejected | `constitute-project`: 'commercial use is expressly prohibited'; use 'solely for providing information to users of this site'. |
| WIPO Lex | rejected | `wipo-lex`: terms 2.2 forbid automated queries, bulk acquisition/storing and web scraping. |
| FAOLEX | planned (HTTP 403) | `faolex`. |
| ILO NATLEX | planned (robots.txt HTTP 403) | `ilo-natlex`. |
| UNHCR Refworld | planned (robots.txt HTTP 403) | `unhcr-refworld`. |
| Laws.Africa LII network (ULII, ZambiaLII, MalawiLII ...) | rejected (earlier descriptor `laws-africa-lii-network`) | ULII terms read again in this run: 'Scraping and bulk downloading from this website is strictly prohibited.'; robots.txt `Content-Signal: ai-train=no, search=yes, ai-input=no`; licence CC BY-NC. |
| ICL Bern (servat.unibe.ch/icl) | not used | translations marked '(c) 1994-2020 A. Tschentscher'; its copyright page is a dead link. |
| Legislationline (ODIHR/OSCE) | not used | refers to the OSCE copyright policy; third-party translations. |
| Venice Commission / CODICES | not used | robots.txt answered HTTP 502. |
| Wikisource | not used | volunteer transcriptions of mixed provenance and currency; the status of each text is not clear. |
| ACE Project (aceproject.org) | not used | Crawl-delay 30-90 s; electoral site. |

## Findings per loaded publisher (as recorded in the descriptor)

The text below is the header of each source descriptor: the robots.txt and the terms as read, in the publisher's words.

### Armenia - `am-concourt-constitution`

```text
Constitution of the Republic of Armenia, English text "with amendments", from the website of the Constitutional Court. Read on 2026-10-15:
  https://concourt.am/robots.txt - HTTP 404 (no robots.txt): no crawling rules to apply. The connector reads it anyway.
  https://concourt.am/en (page footer): "(c) 2021-2026. All rights reserved. The images posted on this website are protected by the law of the Republic of Armenia
       on copyright and related rights. It is forbidden to reproduce, distribute, illustrate, adopt or to redesign the posted photos in any other way, as well as to
       use it in other ways unless permission by the S..." - the reservation is about the site's photographs; no terms page about texts was found.
  https://concourt.am/en/normative-legal-bases/constitution-of-ra - the page lists three versions ("Constitution with the Amendments adopted by the Referendum of 6 December 2015",
       "... of 27 November 2005", "Constitution adopted by the Referendum of 5 July 1995") and prints the current one under the heading "CONSTITUTION OF THE REPUBLIC OF ARMENIA
       (WITH AMENDMENTS) Adopted by the Referendum of 06.12.2015 Amended by the National Assembly on 22.06.2020". It ends with the amending law's last article and
       "Source: http://www.translation-centre.am/pdf/Translat/RA_Constitution/RA_Constitution_2015_en.pdf".
```

### Bangladesh - `bd-bdlaws-constitution`

```text
Constitution of Bangladesh from the Legislative and Parliamentary Affairs Division of Bangladesh (Laws of Bangladesh) (bdlaws.minlaw.gov.bd). Read on 2026-10-15, before any document was fetched:
  robots.txt - bdlaws.minlaw.gov.bd/robots.txt: "User-agent: *" "Allow: /" (two rules, nothing disallowed for the act pages); no Crawl-delay
  Terms - page footer: "Copyright © 2019, Legislative and Parliamentary Affairs Division, Ministry of Law, Justice and Parliamentary Affairs" - a generic footer; no terms-of-use, licence or disclaimer page is linked from the home page or the page of the Constitution
  The document - The page "The Constitution of the People's Republic of Bangladesh" prints the English text with the amendments applied, the footnote reference of each amendment before the amended passage ("5[2A. The state religion ...") and the footnotes at the end ("Substituted for the words ... by the Constitution (Fifteenth Amendment) Act, 2011 (Act XIV of 2011), section 3"). It prints no edition date. Article 153(2) says that the authentic text is the Bengali one and that the English text is an authorised translation ("in the event of conflict between the Bengali and the English text, the Bengali text shall prevail").
```

### China - `cn-govcn-constitution`

```text
Constitution of the People's Republic of China (Chinese) from the central government portal. Pages read on 2026-10-15,
before the constitution page was fetched:
  https://www.gov.cn/robots.txt - "User-agent: *", "Allow: /1", then Disallow lines for /2016gov/, /2016shuju/,
       /2016guoqing/, /2016zhengce/, /premier/, /guowuyuan/..., /guoqing/2013-03/16/content_2583121.htm and others; no
       Crawl-delay; the page used here (/guoqing/2018-03/22/content_5276318.htm) is not disallowed.
  https://www.gov.cn/home/2014-02/23/content_5046258.htm - 网站声明 (website statement), linked from every page footer:
       "本网站内容由中央和地方各级政府、政府网站以及相关单位提供。任何媒体、互联网站和商业机构不得利用本网站发布的内容进行商业性的原版原式地转载，
       也不得歪曲和篡改本网站所发布的内容。本网站所涉及到的版权归本网站所属。 本网站提供的资料如与相关纸质文本不符，以纸质文本为准。"
       (Content is supplied by central and local governments and related units. No media, website or commercial body may
       use the content published on this site for commercial reposting of the original as it is, nor distort or tamper with
       it. The copyright of this site belongs to it. Where the material differs from the printed text, the printed text
       prevails.)
  The constitution page itself (source line "来源： 新华社") has no further terms. The copyright statute (Copyright Law of
  the PRC, art 5(1): laws, regulations and other documents of a legislative nature and their official translations are
  outside the Law's protection) was not read on a publisher's page; marked "to verify".
```

### Hong Kong SAR - `hk-cmab-basiclaw`

```text
The Basic Law of the Hong Kong Special Administrative Region (English) from the Basic Law website of the Constitutional and
Mainland Affairs Bureau. Pages read on 2026-10-15, before any document was fetched:
  https://www.basiclaw.gov.hk/robots.txt - HTTP 200 with no rules for any agent; no Crawl-delay.
  https://www.basiclaw.gov.hk/en/notices/index.html - "Important Notices", linked from the footer of every page:
       Disclaimer: "The information contained in this website has no legal status, and is made available for information only
       and should not be relied on as an official version of the Basic Law and related constitutional instruments herein."
       Copyright Notice: "The contents available on this website, including but not limited to all texts, graphics, drawings,
       diagrams, photographs and compilation of data or other materials are protected by copyright. The Government of the Hong
       Kong Special Administrative Region is the owner of all copyright works contained in this website. Any reproduction,
       adaptation, distribution, dissemination or making available of such copyright works to the public for commercial
       purposes is strictly prohibited unless prior written authorization has been obtained from the Secretary for
       Constitutional and Mainland Affairs. Permission is however given for the dissemination or reproduction of information
       contained in this website for non-commercial purposes provided that the Constitutional and Mainland Affairs Bureau as the
       source of the information is acknowledged."
  https://www.basiclaw.gov.hk/en/basiclaw/index.html - the contents page: the text is offered as one HTML page per part
       (decree, preamble, nine chapters, three annexes, other instruments) and as a PDF booklet ("Important Notice: The
       information contained in this booklet has no legal status"); the HTML pages are used, the booklet is not.
```

### Indonesia - `id-mpr-constitution`

```text
Constitution of Indonesia from the MPR (People's Consultative Assembly) Secretariat General (www.mpr.go.id). Read on 2026-10-15, before any document was fetched:
  robots.txt - www.mpr.go.id/robots.txt names only "facebookexternalhit" (with an empty "Disallow:"); no rule applies to other agents; no Crawl-delay
  Terms - home page footer: "HAK CIPTA (c) SEKRETARIAT JENDERAL MPR RI" (copyright, Secretariat General of the MPR) - a generic footer; no terms-of-use or licence page was found
  The document - The PDF is headed "MAJELIS PERMUSYAWARATAN RAKYAT SEKRETARIAT JENDERAL UNDANG-UNDANG DASAR NEGARA REPUBLIK INDONESIA TAHUN 1945 DALAM SATU NASKAH" (the Constitution in one text, with the four Amendments, Perubahan Pertama to Keempat, marked by *), **), ***), ****) after the provisions they changed); it carries no copyright notice. The font prints the hyphen of "undang-undang" as a soft-hyphen glyph (U+00AD), which the parser reads as a hyphen (hint soft_hyphen).
```

### Kenya - `ke-parliament-constitution`

```text
Constitution of Kenya from the Parliament of Kenya (copy of the National Council for Law Reporting's edition) (www.parliament.go.ke). Read on 2026-10-15, before any document was fetched:
  robots.txt - www.parliament.go.ke/robots.txt: the standard Drupal file, one "User-agent: *" group disallowing /core/, /profiles/, /admin/, /comment/reply/, /search/, /user/... and similar paths; no Crawl-delay; /sites/default/files/ is not disallowed
  Terms - page footer: "© Copyright Parliament of Kenya. All rights reserved." - a generic footer; no terms-of-use, licence or disclaimer page was found on the pages read (home page, glossary)
  The document - The PDF is headed "LAWS OF KENYA THE CONSTITUTION OF KENYA, 2010 Published by the National Council for Law Reporting with the Authority of the Attorney-General www.kenyalaw.org"; its file properties give the title "Rev. 2010]". It prints no copyright notice and no edition date. The text of the Constitution is followed by the Schedules and by the Promulgation by the President (the referendum of 4 August 2010; in effect from 27 August 2010).
```

### Kyrgyzstan - `kg-shailoo-constitution`

```text
Constitution of the Kyrgyz Republic (Russian text, in force from 5 May 2021) as a PDF on the site of the Central Commission for Elections and
Referenda. Read on 2026-10-15:
  https://www.shailoo.gov.kg/robots.txt - one rule only (the file has a single line); no Crawl-delay; the PDF path is not disallowed.
  https://www.shailoo.gov.kg/ (page footer): "© 2026 Кыргыз Республикасынын Шайлоо жана референдум өткөрүү боюнча борбордук комиссиясы. Бардык укуктар корголгон."
       (all rights reserved) - a generic footer; no terms-of-use, licence or copyright page was found.
The PDF itself starts "КОНСТИТУЦИЯ КЫРГЫЗСКОЙ РЕСПУБЛИКИ (Введена в действие Законом Кыргызской Республики от 5 мая 2021 года)" and ends with the signature of the
President of the Kyrgyz Republic (S. Zhaparov). It carries no copyright notice.
```

### Lebanon - `lb-presidency-constitution`

```text
Constitution of Lebanon from the Presidency of the Lebanese Republic (www.presidency.gov.lb). Read on 2026-10-15, before any document was fetched:
  robots.txt - www.presidency.gov.lb/robots.txt: HTTP 200 with five rules for "User-agent: *", none of which covers /lebanon-system/; no Crawl-delay
  Terms - no terms-of-use, copyright or licence page was found: the footer links to "سياسة الخصوصية" (privacy policy, which concerns the mobile application's notifications) and to a contact page only, and the home page and the page of the Constitution carry no copyright line
  The document - The page "الدستور اللبناني" (menu "النظام اللبناني") prints the text in six Parts (الباب) with the notes of the amending constitutional laws in brackets after the number of each article (for example "المادة 5 (المعدلة بالقانون الدستوري الصادر في 7/12/1943)"), the preamble with the note "(أضيفت هذه المقدمة إلى الدستور اللبناني بموجب القانون الدستوري الصادر في 21/9/1990)", and a note "(تعديل المادة بتاريخ 4/9/2004)" before article 49. It prints no date of its own edition. Articles repealed by amendment are printed as a heading with the note "(ألغيت ...)", several at once ("المواد 90 و91 و92").
```

### Macao SAR - `mo-dsaj-basiclaw`

```text
Constitution of Macao Special Administrative Region from the Legal Affairs Bureau (DSAJ) of the Macao SAR, Boletim Oficial (bo.dsaj.gov.mo). Read on 2026-10-15, before any document was fetched:
  robots.txt - bo.dsaj.gov.mo/robots.txt: "User-agent: *" "Disallow:" (an empty Disallow: nothing is disallowed); no Crawl-delay. (www.dsaj.gov.mo answers with a challenge page and is not used.)
  Terms - page footer of www.bo.dsaj.gov.mo: "Copyright © DSAJ 2026" - a generic footer; no terms of use, licence or disclaimer page was found on the home page, the legislation list or the Basic Law pages
  The document - The English page is headed "(Unofficial Translation, For Reference Only)"; the Portuguese and Chinese pages carry no such label and no statement about authenticity. All three give "(Adopted by the Eighth National People's Congress at its First Session on 31 March 1993)" (Portuguese: "... para entrar em vigor no dia 20 de Dezembro de 1999").
```

### Nepal - `np-lawcommission-constitution`

```text
Constitution of Nepal from the Nepal Law Commission (giwmscdnone.gov.np). Read on 2026-10-15, before any document was fetched:
  robots.txt - giwmscdnone.gov.np/robots.txt answered HTTP 404 (none); lawcommission.gov.np/robots.txt, which links the file: "User-agent: *" "Crawl-delay: 10" and one more rule - the manifest therefore waits 10 s between requests
  Terms - no copyright notice, terms of use or licence was found on the home page or on the page of the Constitution (lawcommission.gov.np/content/13437/nepal-s-constitution/), and none in the PDF, whose pages carry the line "www.lawcommission.gov.np"
  The document - The first page of the PDF reads "The Constitution of Nepal / Date of Publication in Nepal Gazette / September 20, 2015 / Amendments: 1. The Constitution of Nepal (First Amendment), 2016 / February 28, 2016 / 2. The Constitution of Nepal (Second Amendment), 2020 / June 18, 2020". It is an English text on the Law Commission's page headed "Constitution of Nepal (2nd amd. English)"; no statement about its legal status is printed.
```

### Pakistan - `pk-cci-constitution`

```text
Constitution of Pakistan from the Council of Common Interests of Pakistan (cci.gov.pk). Read on 2026-10-15, before any document was fetched:
  robots.txt - cci.gov.pk/robots.txt answered HTTP 404 (no robots.txt): no crawling rules to apply; the connector reads it anyway
  Terms - home page footer: "Copyright (c)2026 All Rights" [Reserved] - a generic footer; no terms-of-use or licence page was found
  The document - The PDF cover reads "THE CONSTITUTION OF THE ISLAMIC REPUBLIC OF PAKISTAN [As modified upto the 31st May, 2018] NATIONAL ASSEMBLY OF PAKISTAN"; it carries no copyright notice. The National Assembly's own site (na.gov.pk, which lists an edition up to 21 November 2025) reset the connection from this sandbox and was not used; a Sindh Government copy "as amended upto the Twenty-sixth Amendment" (sindhlaws.gov.pk) has an unreliable text-layer order and was not used.
```

### Senegal - `sn-cc-constitution`

```text
Constitution of Senegal from the Conseil constitutionnel of Senegal (conseilconstitutionnel.sn). Read on 2026-10-15, before any document was fetched:
  robots.txt - conseilconstitutionnel.sn/robots.txt: "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php"; no Crawl-delay; /la-constitution/ is not disallowed
  Terms - no terms-of-use, licence or copyright page was found on the pages read (the page "La constitution" and its menu); the site is the Council's own
  The document - The page "La constitution" opens with a "Note explicative" signed by the President of the Council: the Constitution, "adoptée lors du référendum du 7 janvier 2001, promulguée et publiée au Journal officiel n° 5963 du 22 janvier 2001", was amended by successive constitutional laws and "aucune action concertée des autorités compétentes" produced a single text; "Face à cette situation ... le Conseil constitutionnel" therefore recorded the constitutional laws adopted so far (from n° 2003-15 of 19 June 2003 to n° 2018-14 of 11 May 2018) and "intégrer, par ordre de date, les modifications" into the text. The Council notes that its consolidated text is not an official publication ("l'absence de ce travail officiel de consolidation"), and lists imperfections of the amending laws it kept as they are.
```

### Sri Lanka - `lk-parliament-constitution`

```text
Constitution of Sri Lanka, English, "as amended up to 31st October 2022", Revised Edition 2023, as a PDF on the Parliament's website. Read on 2026-10-15:
  https://www.parliament.lk/robots.txt - "User-agent: *" "Disallow: /adminpanel" "Disallow: /api" "Disallow: /preview" "Allow: /"; no Crawl-delay. /files/pdf/ is not disallowed.
  https://www.parliament.lk/ (page footer): "Copyright (c) The Parliament of Sri Lanka. All Rights Reserved." - a generic footer; no terms-of-use, licence or copyright page was found.
  The PDF (306 pages, text layer): cover "THE CONSTITUTION OF THE DEMOCRATIC SOCIALIST REPUBLIC OF SRI LANKA (As amended up to 31st October 2022) Revised Edition - 2023 Published by the
       Parliament Secretariat Printed at the Department of Government Printing"; page iii: "This unofficial edition edited by the Bills Office of the Legislative Services
       Department of Parliament of Sri Lanka reproduces the text of the Constitution of the Democratic Socialist Republic of Sri Lanka as amended by Parliament from time to
       time up to the Twenty First Amendment to the Constitution. The footnotes below the text indicate the particular Amendments to the Constitution by which such Amendments
       have been made." No copyright notice in the PDF.
  beta.parliament.lk/files/pdf/constitution.pdf (the address a search engine returned) answered 502 through the sandbox proxy and was not used.
```

### Thailand - `th-senate-constitution`

```text
Constitution of Thailand from the Secretariat of the Senate of Thailand (www.senate.go.th). Read on 2026-10-15, before any document was fetched:
  robots.txt - www.senate.go.th/robots.txt: "User-agent: *" with Disallow lines for /v2/, /document/, /webstatic/, /analytics/ and similar; no Crawl-delay; /assets/portals/1/fileups/ is not disallowed
  Terms - home page footer: "ลิขสิทธิ์ © 2569 สำนักงานเลขาธิการวุฒิสภา | The Secretariat of the Senate" (copyright 2026, Secretariat of the Senate) - a generic footer; the only legal page linked is the personal-data protection notice (noticePDPA.pdf); no terms of use or licence
  The document - The PDF "Constitution2560.pdf" is headed "Official Translation CONSTITUTION OF THE KINGDOM OF THAILAND*" and ends "Certified Translation (Mr. Distat Hotrakitya) Secretary-General of the Council of State"; its remark reads "This translation has been prepared by the Office of the Council of State for information purposes only. Whilst the Office of the Council of State has made efforts to ensure the accuracy and correctness of the translation, the original Thai text as formally adopted and published shall in all events remain the sole authoritative text having the force of law." No copyright notice in the PDF.
```

### Uzbekistan - `uz-constitution-site`

```text
Constitution of the Republic of Uzbekistan (2023 edition) from the national portal https://constitution.gov.uz. Read on 2026-10-15:
  https://constitution.gov.uz/robots.txt - HTTP 404 (no robots.txt): no crawling rules to apply. The connector reads it anyway.
  https://constitution.gov.uz/ru (page footer, Russian): "При использовании материалов ссылка на konstitutsiya.gov.uz обязательна. Все права
       защищены" (When using the materials a link to konstitutsiya.gov.uz is mandatory. All rights reserved). The page links
       the President's website and the Government portal under "Полезные ссылки". No terms-of-use page was found; the
       privacy policy ("Политика конфиденциальности") is the only legal page linked.
  The page offers the Constitution as PDF in five versions (/uploads/constitution_ru.pdf, _en.pdf, _uz.pdf [Uzbek in Cyrillic],
       _oz.pdf [Uzbek in Latin], _qr.pdf [Karakalpak]). The English file's first line reads "Unofficial translation".
The 2023 Constitution is described on the first page of each version: "Настоящая новая редакция Конституции Республики Узбекистан
принята путем всенародного голосования на референдуме Республики Узбекистан, проведенном 30 апреля 2023 года."
```

### Vietnam - `vn-chinhphu-constitution`

```text
Constitution of Vietnam from the Government Electronic Newspaper of Vietnam (Chinhphu.vn) (xaydungchinhsach.chinhphu.vn). Read on 2026-10-15, before any document was fetched:
  robots.txt - xaydungchinhsach.chinhphu.vn/robots.txt: "User-agent: *" "Allow: /" (nothing disallowed); no Crawl-delay
  Terms - page footer: "© BÁO ĐIỆN TỬ CHÍNH PHỦ ... Giấy phép số: 19/GP-CBC, cấp ngày 10/5/2024 ... Bản quyền thuộc Báo Điện tử Chính phủ - Cổng Thông tin điện tử Chính phủ." (copyright belongs to the Government Electronic Newspaper - the Government Portal) - a copyright line of a news site; no terms-of-use, licence or reuse page was found on the page read
  The document - The page is an article headed "TOÀN VĂN: Hiến pháp nước Cộng hòa xã hội chủ nghĩa Việt Nam", dated 28/12/2023 07:07, with the lead "(Chinhphu.vn) - Toàn văn Hiến pháp nước Cộng hòa xã hội chủ nghĩa Việt Nam (2013)." It prints the text of the 2013 Constitution and, at its end, the line "Hiến pháp này đã được Quốc hội nước Cộng hòa xã hội chủ nghĩa Việt Nam khóa XIII, kỳ họp thứ 6 thông qua ngày 28 tháng 11 năm 2013."
```

### South Africa - `za-doj-constitution`

```text
Constitution of the Republic of South Africa, 1996, from the Department of Justice and Constitutional Development's site
"The South African Constitution" (https://www.justice.gov.za/constitution/). Pages read on 2026-10-15 before any PDF was fetched:
  https://www.justice.gov.za/robots.txt - "User-agent: *" "Disallow: /admin/ /login/ /cart/ /checkout/ /search/ /cgi-bin/"
       "Allow: /"; no Crawl-delay. The PDFs under /constitution/docs/ are not disallowed.
  https://www.justice.gov.za/about_web/terms_conditions.htm - "Usage and copyright": "Users may view, copy, download to a local
       drive, print and distribute the content of this website, or any part thereof only for non-commercial informational or
       reference purposes. Users who wish to use content from this site for commercial purposes may only do so with the prior
       written permission obtained from the web manager ... Any reproduction and/or copy of the content or portion thereof for
       commercial purposes must include this copyright notice in its entirety: (c) Department of Justice and Constitutional
       Development: ALL RIGHTS RESERVED. All moral and any other rights of the ... (DoJCD) or other legal entity ... not
       expressly granted are reserved."
  https://www.justice.gov.za/constitution/pdf.html - "Full Text Translations" in the eleven languages (the PDFs under
       docs/30y-2026/); "Latest amednment: Constitution Seventeenth Amendment Act of 2012. Text as Monday, 25 May, 2026".
The same terms are used by https://www.gov.za (GCIS) and were recorded for the legislation portal in za-gov-legislation.
```

## Notes for the maintainers

* Overlap with the Americas/Europe/Caucasus sweep: Armenia (the same site, concourt.am), Kyrgyzstan and Uzbekistan (other sites) appear in both sweeps under different publisher ids. Both write `/{cc}/constitution` work URIs, so only one source per country should be active; the parsers differ (`constitution_text` here, `constitution_pages_b` there) and could be unified.
* Hints of the shared parser that were added for these publishers: `interlude_regex`, `max_gap`, `max_first`, `heading_before: line`, `heading_before_regex`, `heading_wrap`, `html_replace` (markup repairs applied before parsing, never to words), multi-page documents (`parts`), footnote marks before numbers (`5[2A.`), Bengali digits/danda/letters, NFC normalisation, soft hyphens and bidi marks.
* The earlier planned descriptor `bd-bdlaws` (all laws) is unchanged; `bd-bdlaws-constitution` covers the Constitution alone under the AMBER reading.
* Known weak spots: Kenya's marginal headings are recognised by shape (a few may be mis-assigned, no text is lost); the Lebanese list headings of repealed articles ('المواد 90 و91 و92') are stored under the first number; Pakistan's text is 'as modified up to 31 May 2018' and Nepal's English text is a translation.
