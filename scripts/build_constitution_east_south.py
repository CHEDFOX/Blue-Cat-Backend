# ruff: noqa: E501 - the findings are the publishers' own words, kept on one line each
"""Writes the source descriptors, manifests and minimal profiles of the East and Southern Africa constitutions sweep.

    python scripts/build_constitution_east_south.py

One publisher = one source descriptor (``config/sources/<id>.yaml``) + one manifest (``config/manifests/<id>.yaml``,
read by the ``constitution_docs`` connector); one minimal draft profile per country that has none yet
(``config/jurisdictions/<cc>.yaml``). The findings below are the publishers' own words, read on 2026-10-15 before
any document was fetched (docs/constitutions-africa-east-south-deep.md). Re-running the script rewrites the files.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
READ_ON = "2026-10-15"

LANG_NAMES = {
    "en": "English",
    "fr": "French",
    "pt": "Portuguese",
    "sw": "Swahili",
    "so": "Somali",
    "rw": "Kinyarwanda",
    "mg": "Malagasy",
}


def _doc(**kw: Any) -> dict[str, Any]:
    return kw


# --------------------------------------------------------------------------- the publishers

PUBLISHERS: list[dict[str, Any]] = [
    {
        "id": "rw-minijust-constitution",
        "cc": "RW",
        "host": "www.minijust.gov.rw",
        "short": "Ministry of Justice of Rwanda",
        "publisher": "Ministry of Justice of the Republic of Rwanda (www.minijust.gov.rw), Publications / Laws",
        "licence_id": "RW-official-text-no-terms",
        "licence_url": "https://www.minijust.gov.rw/",
        "crawl": 5,
        "findings": [
            'robots.txt - www.minijust.gov.rw/robots.txt: a TYPO3 file; the "User-agent: *" group disallows URLs with query ids, /typo3temp/ (except css, js and images), /fileadmin/templates/html/*, *.sql and a few technical paths; further groups disallow named crawlers (none is the ULIS agent); no Crawl-delay; /fileadmin/user_upload/Minijust/Publications/Laws/ is not disallowed',
            'Terms - page footer: "© 2026 Government of the Republic of Rwanda" - a generic footer; no terms-of-use, licence or disclaimer page is linked from the home page',
            'The document - the PDF is the Official Gazette n° Special of 04/08/2023 ("Umwaka wa 62 / Year 62 / 62ème Année"), 149 pages: the Constitution of the Republic of Rwanda in three parallel columns, Kinyarwanda | English | French, on landscape pages. The connector fetches the file once per language and the parser reads one column (crop) of every page. Last-Modified of the file: 8 Sep 2023.',
        ],
        "known": [
            "The Official Gazette is printed in three parallel columns; the parser cuts out the column of the language by its position on the page. Justified lines are broken into words in places, so a few headings come out garbled or out of order (for example article 1 in English, and the headings of articles 16, 132 and 169 in English, 48, 59, 115 and 117 in French, several in Kinyarwanda): the text of such an article stays in the preceding article and its note names the missing numbers.",
            "The Kinyarwanda column is read with the article word 'Ingingo ya'; about 19 article headings were not recognised there (their text is kept in the preceding article).",
            "Schedules do not exist in this Constitution; the signatures at the end of the gazette are not stored.",
        ],
        "documents": [
            _doc(
                slug="rw-rw",
                language="rw",
                title="Itegeko Nshinga rya Repubulika y'u Rwanda",
                citation_name="Rwanda Constitution",
                crop=[88, 0, 226, 595],
                structure_extra={
                    "keywords": ["Ingingo ya"],
                    "chapter_regex": "^UMUTWE WA ",
                    "preamble_regex": "^IRANGASHINGIRO$",
                },
            ),
            _doc(
                slug="rw-en",
                language="en",
                title="Constitution of the Republic of Rwanda",
                citation_name="Rwanda Constitution",
                crop=[314, 0, 220, 595],
                structure_extra={"keywords": ["Article"], "chapter_regex": "^(CHAPTER|SECTION) (ONE|[IVX0-9]+)"},
            ),
            _doc(
                slug="rw-fr",
                language="fr",
                title="Constitution de la République du Rwanda",
                citation_name="Rwanda Constitution",
                crop=[534, 0, 224, 595],
                structure_extra={"keywords": ["Article"], "chapter_regex": "^(CHAPITRE|SECTION) (PREMIER|[IVX0-9]+)"},
            ),
        ],
        "url": "https://www.minijust.gov.rw/fileadmin/user_upload/Minijust/Publications/Laws/Constitution.pdf",
        "format": "pdf",
        "common": {
            "edition_date": "2023-08-04",
            "edition_label": "Official Gazette n° Special of 04/08/2023 (the Constitution as revised; the gazette date is the edition date)",
            "authoritative": False,
            "text_origin": "official_authentic",
        },
        "common_structure": {
            "first_page": 2,
            "page_numbers": True,
            "drop_lines": ["^Official Gazette n"],
            "unit_kind": "article",
            "style": "keyword",
            "heading": "same_line",
        },
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text; no permission for automated retrieval is stated either",
        "text_note": "the Constitution of the Republic of Rwanda as printed in the Official Gazette n° Special of 04/08/2023, copy on the Ministry of Justice site",
    },
    {
        "id": "mg-assemblee-constitution",
        "cc": "MG",
        "host": "www.assemblee-nationale.mg",
        "short": "Assemblée nationale de Madagascar",
        "publisher": "Assemblée nationale de Madagascar (www.assemblee-nationale.mg), archive of texts",
        "licence_id": "MG-official-text-no-terms",
        "licence_url": "https://www.assemblee-nationale.mg/",
        "crawl": 5,
        "findings": [
            'robots.txt - www.assemblee-nationale.mg/robots.txt: "User-agent: *" "Allow: /" "Allow: /opendata/$" "Allow: /opendata/licence/$" "Disallow: /opendata/api/"; no Crawl-delay; /media/archives_textes/ is allowed',
            'Terms - page footer: "© 2025 Assemblée Nationale de Madagascar. Tous droits réservés." (links: Plan du site, Open Data, Politique de confidentialité, Politique de cookies). The Open Data page (https://www.assemblee-nationale.mg/opendata/licence/) says: "Les jeux de données publiés sur le portail Open Data de l\'Assemblée Nationale de Madagascar sont mis à disposition sous la Licence Ouverte version 2.0" - that licence is stated for the datasets of the Open Data portal; the PDF of the Constitution is in the archive of texts (/media/archives_textes/) and is not said to be one of them, so it is not relied on here',
            'The document - the PDF is headed "PRESIDENCE DE LA REPUBLIQUE - CONSTITUTION DE LA QUATRIEME REPUBLIQUE DE MADAGASCAR - REFERENDUM LE 17 NOVEMBRE 2010": the French text (pages 1-33) is followed by the Malagasy text (pages 34-75, headed "LALAMPANORENAN\'NY REPOBLIKA FAHAEFATRA - FITSAMPAN-KEVIBAHOAKA NY FAHA-17 NOVEMBRE 2010"). Last-Modified of the file: 16 Mar 2026.',
        ],
        "known": [
            "The PDF is the text of the referendum of 17 November 2010 as published by the Presidency; it prints no edition date and amendments adopted since (if any) are not applied. The political transition of 2025 (to verify) is not reflected.",
            "Both languages come from one PDF (French pages 1-33, Malagasy pages 34-75). The heading of article 164 is printed one word per line in the French text, and three headings of the Malagasy text carry typing errors ('Adnininy 4', ...): their text stays in the preceding article and the note says so.",
        ],
        "documents": [
            _doc(
                slug="mg-fr",
                language="fr",
                title="Constitution de la Quatrième République de Madagascar",
                citation_name="Madagascar Constitution",
                structure_extra={"last_page": 33, "keywords": ["Article"], "tail_regex": "^Antananarivo, le"},
            ),
            _doc(
                slug="mg-mg",
                language="mg",
                title="Lalàmpanorenan'ny Repoblika Fahaefatra",
                citation_name="Madagascar Constitution",
                structure_extra={"first_page": 34, "keywords": ["Andininy"], "preamble_regex": "^SAVARANONANDO"},
            ),
        ],
        "url": "https://www.assemblee-nationale.mg/media/archives_textes/pl/sans-numero/Constitution-de-la-IV%C3%A8me-R%C3%A9publique.pdf",
        "format": "pdf",
        "common": {
            "adopted_on": "2010-11-17",
            "edition_label": "text of the referendum of 17 November 2010 as published by the Presidency of the Republic; no edition date printed",
            "events": [
                {
                    "type": "adoption",
                    "date": "2010-11-17",
                    "description": "REFERENDUM LE 17 NOVEMBRE 2010",
                    "locator": "title page",
                }
            ],
            "authoritative": False,
            "text_origin": "official_authentic",
        },
        "common_structure": {"unit_kind": "article", "style": "keyword"},
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text beyond a generic 'tous droits réservés' footer; no permission for automated retrieval is stated either",
        "text_note": "the Constitution of the Fourth Republic of Madagascar (referendum of 17 November 2010), French and Malagasy, as published by the Presidency and kept in the archive of texts of the National Assembly",
    },
    {
        "id": "mu-assembly-constitution",
        "cc": "MU",
        "host": "assembly-rra.govmu.org",
        "short": "Rodrigues Regional Assembly (Mauritius)",
        "publisher": "Rodrigues Regional Assembly, Government of Mauritius (assembly-rra.govmu.org), Legislations: The Constitution",
        "licence_id": "MU-govmu-copyright-notice",
        "licence_name": "Government of Mauritius Copyright Notice: State material may be downloaded without specific permission, reproduced accurately and not in a misleading context, with the source (URL) and copyright status acknowledged; any other use subject to approval",
        "licence_url": "https://assembly-rra.govmu.org/Pages/CopyrightNotice.aspx",
        "crawl": 5,
        "findings": [
            "robots.txt - assembly-rra.govmu.org/robots.txt: HTTP 404 (no robots.txt): no crawling rules to apply. The connector reads it anyway.",
            'Terms - https://assembly-rra.govmu.org/Pages/CopyrightNotice.aspx: "The Government of Mauritius (the “State”) encourages public access to government information on this website and other government websites. However, all material published on this website is protected so far as is possible by the provisions of the Copyright Act in favour of the State. Apart from any use permitted by the Copyright Act, the State copyright protected material may be downloaded free of charge to file or printer without requiring specific permission, provided that any such State owned material being reproduced accurately and not being used in a derogated manner or in a misleading context. Where this material is being issued to others, the source (including the URL) and copyright status must be acknowledged. The permission to reproduced State copyright protected material does not extend to any material on this website that is identified as being the Copyright of a third party. Any other proposed use of any State protected material is subject to approval." Page footer: "© Government of Mauritius 2020. All rights reserved." The Disclaimer page (/Pages/Disclaimer.aspx) disclaims accuracy and completeness.',
            'The document - the PDF (131 pages, Last-Modified 3 Sep 2025, file name constitution_1968_revised_updated2025.pdf) is headed "THE CONSTITUTION GN 54/1968 12 March 1968 (Schedule to THE MAURITIUS INDEPENDENCE ORDER 1968)". It prints no edition date. The host is the site of the Rodrigues Regional Assembly, an organ of the Government of Mauritius; the National Assembly\'s site (mauritiusassembly.govmu.org) carries an older upload.',
        ],
        "known": [
            "The copy on the Rodrigues Regional Assembly's site, not the Attorney General's Revised Laws; it prints no edition date (the file name says 'revised updated 2025'), so the retrieval date is the edition date.",
            "The sections are numbered on a line of their own with the marginal heading below them; the table of contents and the two-column arrangement of sections at the head of the PDF are not stored. Schedules and the oaths are stored as blocks of paragraphs. Amendment notes printed in the margin ('Amended by [Act No. 48 of 1991]') are part of the stored text.",
        ],
        "documents": [
            _doc(
                slug="mu-en",
                language="en",
                title="The Constitution of Mauritius",
                citation_name="Mauritius Constitution",
                structure_extra={
                    "style": "numdot",
                    "unit_kind": "section",
                    "heading_lines": 1,
                    "chapter_regex": "^CHAPTER [IVX]+",
                    "start_regex": "^CHAPTER I - THE STATE AND THE CONSTITUTION$",
                },
            )
        ],
        "url": "https://assembly-rra.govmu.org/Documents/Legislations/constitution_1968_revised_updated2025.pdf",
        "format": "pdf",
        "common": {
            "adopted_on": "1968-03-12",
            "edition_label": "file named 'constitution_1968_revised_updated2025' (HTTP Last-Modified 3 Sep 2025); no edition date printed",
            "events": [
                {
                    "type": "commencement",
                    "date": "1968-03-12",
                    "description": "THE CONSTITUTION GN 54/1968 12 March 1968 (Schedule to THE MAURITIUS INDEPENDENCE ORDER 1968)",
                    "locator": "title",
                }
            ],
            "authoritative": False,
            "text_origin": "official_consolidation",
        },
        "common_structure": {"page_numbers": True},
        "licence_reading": "AMBER, leaning GREEN - the Copyright Notice allows State material to be downloaded without specific permission if reproduced accurately, not in a misleading context, with the source (URL) and copyright status acknowledged; any other proposed use is subject to approval",
        "text_note": "the Constitution of Mauritius (GN 54/1968, as revised and updated) from the site of the Rodrigues Regional Assembly",
        "redistribution": "allowed_with_conditions",
    },
    {
        "id": "dj-presidence-constitution",
        "cc": "DJ",
        "host": "www.presidence.dj",
        "short": "Présidence de la République de Djibouti",
        "publisher": "Présidence de la République de Djibouti (www.presidence.dj), Djibouti: La Constitution",
        "licence_id": "DJ-official-text-no-terms",
        "licence_url": "https://www.presidence.dj/",
        "crawl": 10,
        "findings": [
            'robots.txt - www.presidence.dj/robots.txt: "User-agent: *" "Disallow:" "Crawl-delay: 10" "Disallow: /admin/" (58 bytes); the crawl delay of 10 s is honoured; /page/ is not disallowed',
            'Terms - page footer: "Copyright © 2026 Présidence de la République - tout droit reservé. Réalisé par l\' ANSIE" - a generic footer; no terms-of-use, licence or disclaimer page is linked',
            'The document - https://www.presidence.dj/page/la-constitution-de-la-republique-de-djibouti, an HTML page headed "LA CONSTITUTION DE LA RÉPUBLIQUE DE DJIBOUTI - version 2010" that prints the preamble and the articles of Titles I to XII in French. It prints no edition date or adoption date.',
        ],
        "known": [
            "The page is headed 'version 2010' and is a web page of the Presidency, not the Journal officiel. A later revision exists (loi n° 192/AN/25/9ème L portant révision de la Constitution, listed on journalofficiel.dj, which rewrites articles 93, 94 and 96); it is not applied here, and the page's own currency is not stated.",
            "The page gives no dates: no adoption or amendment date is recorded.",
        ],
        "documents": [
            _doc(
                slug="dj-fr",
                language="fr",
                title="La Constitution de la République de Djibouti",
                citation_name="Djibouti Constitution",
                structure_extra={"keywords": ["Article"], "heading": "text"},
            )
        ],
        "url": "https://www.presidence.dj/page/la-constitution-de-la-republique-de-djibouti",
        "format": "html",
        "common": {
            "edition_label": "web page headed 'version 2010'; no edition date printed",
            "authoritative": False,
            "text_origin": "official_consolidation",
        },
        "common_structure": {"unit_kind": "article", "style": "keyword"},
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text; no permission for automated retrieval is stated either",
        "text_note": "the Constitution of Djibouti (version 2010) from the web page of the Presidency",
    },
    {
        "id": "ao-tribunal-constitucional-constitution",
        "cc": "AO",
        "host": "www.tribunalconstitucional.ao",
        "short": "Tribunal Constitucional de Angola",
        "publisher": "Tribunal Constitucional de Angola (www.tribunalconstitucional.ao), Constituição",
        "licence_id": "AO-official-text-no-terms",
        "licence_url": "https://www.tribunalconstitucional.ao/",
        "crawl": 5,
        "findings": [
            "robots.txt - www.tribunalconstitucional.ao/robots.txt: HTTP 404 (no robots.txt): no crawling rules to apply. The connector reads it anyway.",
            'Terms - page footer: "Copyright © 2026 - Tribunal Constitucional de Angola" - a generic footer; no terms-of-use, licence or disclaimer page was found on the pages read (home page). The imprint of the PDF (FICHA TÉCNICA) names the Tribunal Constitucional as owner (PROPRIEDADE), Lexdata - Sistemas e Edições Jurídicas, Lda. as publisher (EDITORA) and Imprensa Nacional - E.P. as printer, and prints no reproduction restriction. (The Assembleia Nacional\'s PDF of the same Constitution carries a prohibition in its imprint and is recorded as rejected in source ao-parlamento-constitution.)',
            "The document - \"CONSTITUIÇÃO DA REPÚBLICA DE ANGOLA - EDIÇÃO ESPECIAL ACTUALIZADA 2022\" (1ª Edição Fevereiro 2022, 130 pages; Last-Modified 29 Aug 2025). Its introductory note says the Constitution of 2010 'sofreu alterações, introduzidas pela Lei n.º 18/21, de 16 de Agosto' and that this edition gathers the content in one text.",
        ],
        "known": [
            "The edition is of February 2022 (only the month is printed; the last day of the month is used as the edition date); amendments after that date, if any, are not applied.",
            "The introductory note, contents and the closing lists of signatories are not stored as articles; text after the last article is kept in blocks. The headings of the articles are the parenthesised titles printed under each article number.",
        ],
        "documents": [
            _doc(
                slug="ao-pt",
                language="pt",
                title="Constituição da República de Angola",
                citation_name="Angola Constitution",
                structure_extra={
                    "keywords": ["Artigo"],
                    "heading_next": "paren",
                    "paragraphs": "dot",
                    "preamble_regex": "^PREÂMBULO$",
                },
            )
        ],
        "url": "https://www.tribunalconstitucional.ao/media/3nypgra0/edicao-especial-actualizada-2022.pdf",
        "format": "pdf",
        "common": {
            "adopted_on": "2010-01-21",
            "edition_date": "2022-02-28",
            "edition_label": "Edição Especial Actualizada, 1ª Edição Fevereiro 2022 (day not printed: the last day of the month is used)",
            "events": [
                {
                    "type": "adoption",
                    "date": "2010-01-21",
                    "description": "Vista e aprovada pela Assembleia Constituinte, aos 21 de Janeiro de 2010",
                    "locator": "closing formula",
                },
                {
                    "type": "promulgation",
                    "date": "2010-02-03",
                    "description": "e, na sequência do Acórdão do Tribunal Constitucional n.º 111/2010, de 30 de Janeiro, aos 3 de Fevereiro de 2010",
                    "locator": "closing formula",
                },
                {
                    "type": "amendment",
                    "date": "2021-08-16",
                    "description": "alterações, introduzidas pela Lei n.º 18/21, de 16 de Agosto",
                    "locator": "Nota introdutória",
                },
            ],
            "authoritative": False,
            "text_origin": "official_consolidation",
        },
        "common_structure": {"unit_kind": "article", "style": "keyword", "page_numbers": True},
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text; no permission for automated retrieval is stated either",
        "text_note": "the Constitution of Angola, special updated edition of 2022 published by the Tribunal Constitucional",
    },
    {
        "id": "so-senate-constitution",
        "cc": "SO",
        "host": "senate.gov.so",
        "short": "Federal Parliament of Somalia, Upper House",
        "publisher": "Federal Parliament of Somalia, Upper House (senate.gov.so), Laws: Constitution of the Federal Republic of Somalia 2026",
        "licence_id": "SO-official-text-no-terms",
        "licence_url": "https://senate.gov.so/",
        "crawl": 5,
        "findings": [
            'robots.txt - senate.gov.so/robots.txt: "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php" and two sitemap lines; no Crawl-delay; /wp-json/ is not disallowed',
            'Terms - page footer: "© 2026 Federal Parliament of Somalia - The Upper House. All Rights Reserved." - a generic footer; no terms-of-use, licence or disclaimer page was found on the pages read',
            "The document - the page https://senate.gov.so/document/constitution-of-the-federal-republic-of-somalia-2026/ (dated 7 September 2026) offers 'CONSTITUTION OF THE FEDERAL REPUBLIC OF SOMALIA 2026, PDF 1.3 MB'; its download link (/wp-json/filedeck/v1/download/12129) leads to /wp-content/uploads/2026/09/DASTUURKA-RASMIGA-AH-EE-2026-1.pdf, 202 pages, in Somali, headed \"DASTUURKA SOOMAALIYA - Muqdisho, Soomaaliya - Maarso 4, 2026\". The Office of the President (villasomalia.gov.so/constitution-2/) says the Constitution was approved by the Federal Parliament on 4 March 2026 and signed into law on 8 March 2026; that page is a summary and holds no text.",
        ],
        "known": [
            "Somali text only; no English or Arabic text of the 2026 Constitution was found on an official site. The Provisional Constitution of 2012 (superseded, according to the Office of the President) is not loaded.",
            "The PDF is described by the Upper House as the 2026 Constitution; the date on its title page (4 March 2026) is recorded as the adoption date. The signing on 8 March 2026 is reported by the Office of the President, not by the document. Puntland and Jubaland have not endorsed the text (press reports; to verify); whether it is final or subject to a referendum is disputed in reports.",
            "The schedules (JADWALKA) are stored as blocks of paragraphs.",
        ],
        "documents": [
            _doc(
                slug="so-so",
                language="so",
                title="Dastuurka Soomaaliya",
                citation_name="Somalia Constitution",
                structure_extra={
                    "keywords": ["Qodobka"],
                    "preamble_regex": "^ARAR$",
                    "tail_regex": "^JADWALKA KOOWAAD\\s*\\(A\\)",
                    "chapter_regex": "^CUTUBKA ",
                    "heading": "same_line",
                },
            )
        ],
        "url": "https://senate.gov.so/wp-json/filedeck/v1/download/12129",
        "format": "pdf",
        "common": {
            "adopted_on": "2026-03-04",
            "edition_label": "'Dastuurka Soomaaliya, Muqdisho, Soomaaliya, Maarso 4, 2026' as published by the Upper House in September 2026",
            "events": [
                {
                    "type": "adoption",
                    "date": "2026-03-04",
                    "description": "Muqdisho, Soomaaliya, Maarso 4, 2026",
                    "locator": "title page",
                }
            ],
            "authoritative": False,
            "text_origin": "official_authentic",
        },
        "common_structure": {"unit_kind": "article", "style": "keyword", "page_numbers": True},
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text; no permission for automated retrieval is stated either",
        "text_note": "the Constitution of the Federal Republic of Somalia of 2026 (Somali), as published by the Upper House of the Federal Parliament",
    },
    {
        "id": "tz-mocla-constitution",
        "cc": "TZ",
        "host": "www.sheria.go.tz",
        "short": "Ministry of Constitutional and Legal Affairs of Tanzania",
        "publisher": "Wizara ya Katiba na Sheria (Ministry of Constitutional and Legal Affairs, MoCLA) of Tanzania (www.sheria.go.tz), Machapisho: Katiba",
        "licence_id": "TZ-official-text-no-terms",
        "licence_url": "https://www.sheria.go.tz/pages/terms-and-conditions",
        "crawl": 5,
        "findings": [
            "robots.txt - www.sheria.go.tz/robots.txt: HTTP 404 (no robots.txt): no crawling rules to apply. The connector reads it anyway.",
            'Terms - https://www.sheria.go.tz/pages/terms-and-conditions ("Vigezo na Masharti"): the terms govern the use of the website, its systems and services ("Kanuni na Masharti yafuatayo yanasimamia matumizi yote ya Tovuti, tovuti za mifumo na huduma ..."); they say nothing about reusing the published texts. The Disclaimer ("Angalizo", /pages/disclaimer) concerns services provided through public and private facilities. Footer: "© 2026 MoCLA, Haki zote zimehifadhiwa." (all rights reserved). The footer\'s copyright link (/pages/copyright) answers \'Haipatikani\' (not found).',
            'The document - the page https://www.sheria.go.tz/publications/constitution (Machapisho > Katiba) lists the Swahili text, a PDF of 113 pages (uploaded 31 Jan 2023) headed "KATIBA YA JAMHURI YA MUUNGANO WA TANZANIA YA MWAKA 1977 (Toleo hili ... limezingatia na kuweka pamoja mabadiliko yote yaliyofanywa katika Katiba ya Muungano tangu ilipotungwa mwaka 1977 hadi tarehe 30 Aprili, 2000. Dar es Salaam, 1 Novemba, 2000 - CHENGE, A.J., Mwanasheria Mkuu ... KIMEPIGWA CHAPA NA MPIGACHAPA WA SERIKALI)".',
        ],
        "known": [
            "The Swahili text consolidated to 30 April 2000 (printed by the Government Printer, 1 November 2000), the latest Swahili consolidation on the Ministry's site; the English revised edition of 2005 (source tz-nao-constitution) is later. Amendments after the respective dates are not applied.",
            "The table of contents is skipped (the text starts at article 1); the appendices (NYONGEZA) are stored as blocks of paragraphs. Marginal notes of the print are part of the page text where the extractor places them.",
        ],
        "documents": [
            _doc(
                slug="tz-sw",
                language="sw",
                title="Katiba ya Jamhuri ya Muungano wa Tanzania ya Mwaka 1977",
                citation_name="Tanzania Constitution",
                structure_extra={
                    "style": "numdot",
                    "start_regex": "^1\\. Tanzania ni nchi moja",
                    "drop_lines": ["^Katiba ya Jamhuri ya Muungano wa Tanzania$", "^_{10,}$"],
                    "chapter_regex": "^(SURA|SEHEMU) YA ",
                    "tail_regex": "^NYONGEZA YA ",
                },
            )
        ],
        "url": "https://www.sheria.go.tz/uploads/documents/sw-1675190712-sw1499671152-Katiba_ya_Tanzania.pdf",
        "format": "pdf",
        "common": {
            "adopted_on": "1977-04-26",
            "edition_date": "2000-11-01",
            "edition_label": "Toleo la Katiba lililozingatia mabadiliko hadi tarehe 30 Aprili, 2000 (Dar es Salaam, 1 Novemba, 2000)",
            "events": [
                {
                    "type": "commencement",
                    "date": "1977-04-26",
                    "description": "Katiba hii itaanza kutumika tarehe 26 Aprili, 1977.",
                    "locator": "ibara ya 152(2)",
                }
            ],
            "authoritative": False,
            "text_origin": "official_consolidation",
        },
        "common_structure": {"unit_kind": "article", "page_numbers": True},
        "licence_reading": "AMBER - the terms concern the website's services and say nothing about reusing the published texts; the footer is a generic 'all rights reserved'",
        "text_note": "the Swahili text of the Constitution of the United Republic of Tanzania of 1977 consolidated to 30 April 2000, from the site of the Ministry of Constitutional and Legal Affairs",
    },
    {
        "id": "tz-nao-constitution",
        "cc": "TZ",
        "host": "www.nao.go.tz",
        "short": "National Audit Office of Tanzania",
        "publisher": "Ofisi ya Taifa ya Ukaguzi (National Audit Office of Tanzania, NAOT) (www.nao.go.tz), uploads",
        "licence_id": "TZ-official-text-no-terms-nao",
        "licence_url": "https://www.nao.go.tz/",
        "crawl": 5,
        "findings": [
            'robots.txt - www.nao.go.tz/robots.txt: "User-agent: *" with an empty rule list (14 bytes): nothing is disallowed; no Crawl-delay',
            'Terms - page footer: "© Copyright 2026 - OFISI YA TAIFA YA UKAGUZI" (links: Mwanzo, Sera ya Faragha, Intranet, Barua Pepe, Maoni na Maulizo, Ripoti Ubadhirifu) - a generic footer; no terms-of-use or licence page about texts',
            "The document - https://www.nao.go.tz/uploads/Constitution_of_the_United_Republic_of_Tanzania_en.pdf (91 pages, Last-Modified 13 Mar 2020): \"THE CONSTITUTION OF THE UNITED REPUBLIC OF TANZANIA OF 1977 - CHAPTER 2 OF THE LAWS - 2005. The following is the official Revised version in English of 'KATIBA YA JAMHURI YA MUUNGANO WA TANZANIA YA MWAKA 1977'. This revised edition of the Constitution of the United Republic of Tanzania incorporates and consolidates all amendments made therein by the Constituent Assembly in 1977 up to the 14th Amendment of 2005 and it is printed and published under section 4 of the Laws Revision Act Chapter 4.\"",
        ],
        "known": [
            "The official revised English edition (Cap. 2) of 2005; only the year is printed, so 31 December 2005 is used as the edition date. The Swahili text is the authentic one; later amendments are not applied here.",
            "A copy on the National Audit Office's site (the Office is not the publisher of the text). The margin column of the print (notes of the amending Acts) is cut off before reading, so the marginal headings of the articles are not stored; articles carry no heading. The schedules are stored as blocks.",
        ],
        "documents": [
            _doc(
                slug="tz-en",
                language="en",
                title="The Constitution of the United Republic of Tanzania of 1977",
                citation_name="Tanzania Constitution",
                structure_extra={
                    "style": "numdot",
                    "crop": [140, 0, 455, 842],
                    "chapter_regex": "^(CHAPTER|PART) [A-Z]+",
                },
            )
        ],
        "url": "https://www.nao.go.tz/uploads/Constitution_of_the_United_Republic_of_Tanzania_en.pdf",
        "format": "pdf",
        "common": {
            "adopted_on": "1977-04-26",
            "edition_date": "2005-12-31",
            "edition_label": "Revised Edition 2005, Chapter 2 of the Laws, up to the 14th Amendment of 2005 (only the year is printed: 31 December 2005 is used)",
            "events": [
                {
                    "type": "commencement",
                    "date": "1977-04-26",
                    "description": "[26th April, 1977)",
                    "locator": "heading",
                }
            ],
            "authoritative": False,
            "text_origin": "official_revised",
        },
        "common_structure": {"unit_kind": "article", "page_numbers": True},
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text; no permission for automated retrieval is stated either",
        "text_note": "the official Revised English edition (2005) of the Constitution of the United Republic of Tanzania, copy on the site of the National Audit Office",
    },
    {
        "id": "na-ombudsman-constitution",
        "cc": "NA",
        "host": "ombudsman.org.na",
        "short": "Office of the Ombudsman of Namibia",
        "publisher": "Office of the Ombudsman of Namibia (ombudsman.org.na), Laws: The Constitution of Namibia",
        "licence_id": "NA-official-text-no-terms",
        "licence_url": "https://ombudsman.org.na/",
        "crawl": 5,
        "findings": [
            'robots.txt - ombudsman.org.na/robots.txt: "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php" "Disallow: /wp-content/uploads/wpo/wpo-plugins-tables-list.json" and two sitemap lines; no Crawl-delay; /download/ is not disallowed',
            "Terms - the page https://ombudsman.org.na/download/the-constitution-of-namibia/ (14 October 2022) shows a cookie notice and a link to the Privacy Policy; no copyright, terms-of-use or licence statement was found on the pages read",
            'The document - the download link of that page leads to /wp-content/uploads/2022/09/The-Constitution-of-Namibia.pdf (96 pages, Last-Modified 8 Sep 2022): "The Constitution of the Republic of Namibia has been amended three times; firstly by Act No. 34 of 1998, known as the Namibian Constitution First Amendment Act 1998; secondly by Act No. 7 of 2010, known as the Namibian Constitution Second Amendment Act, 2010; and thirdly by Act No. 8 of 2014, known as the Namibian Constitution Third Amendment Act, 2014. This printed version of the Constitution incorporates all the amendments to facilitate easy reading thereof. Compiled by: The Office of the Ombudsman, February 2016".',
        ],
        "known": [
            "A compilation by the Office of the Ombudsman (February 2016; only the month is printed, so the last day of the month is used as the edition date), not the Government Printer's text; amendments after 2016, if any, are not applied.",
            "The tables of contents are skipped; the Schedules are stored as blocks of paragraphs. The page is read in layout mode so that the paragraph numbers stay on their text lines.",
        ],
        "documents": [
            _doc(
                slug="na-en",
                language="en",
                title="The Constitution of the Republic of Namibia",
                citation_name="Namibia Constitution",
                structure_extra={
                    "layout": True,
                    "heading": "same_line",
                    "drop_lines": ["^The Constitution of the Republic of Namibia$"],
                    "start_regex": "^PREAMBLE\\s*$",
                    "preamble_regex": "^PREAMBLE\\s*$",
                },
            )
        ],
        "url": "https://ombudsman.org.na/download/the-constitution-of-namibia/?wpdmdl=3819",
        "format": "pdf",
        "common": {
            "edition_date": "2016-02-29",
            "edition_label": "Compiled by the Office of the Ombudsman, February 2016, incorporating the amendments of 1998, 2010 and 2014 (day not printed: the last day of the month is used)",
            "authoritative": False,
            "text_origin": "unofficial_compilation",
        },
        "common_structure": {"unit_kind": "article", "style": "keyword", "keywords": ["Article"], "page_numbers": True},
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text; no permission for automated retrieval is stated either",
        "text_note": "the Constitution of Namibia as compiled by the Office of the Ombudsman (February 2016) with the amendments of 1998, 2010 and 2014",
    },
    {
        "id": "sc-ecs-constitution",
        "cc": "SC",
        "host": "ecs.sc",
        "short": "Electoral Commission of Seychelles",
        "publisher": "Electoral Commission of Seychelles (ecs.sc), Regulations: Constitution of the Republic of Seychelles (Laws of Seychelles, Chapter 42)",
        "licence_id": "SC-official-text-no-terms",
        "licence_url": "https://ecs.sc/",
        "crawl": 10,
        "findings": [
            'robots.txt - ecs.sc/robots.txt: "User-agent: *" "Crawl-delay: 10" "Disallow: /admin/" "Disallow: /includes/" "Disallow: /config/" "Disallow: /logs/" "Disallow: /backup/" "Disallow: /.env" "Disallow: /wp-admin/" "Disallow: /wp-includes/"; the crawl delay of 10 s is honoured; /wp-content/uploads/ is not disallowed',
            'Terms - page footer: "Electoral Commission Seychelles © 2024. All Rights Reserved. Designed by Unified Technologies" - a generic footer; no terms-of-use, licence or disclaimer page was found on the pages read',
            'The document - https://ecs.sc/wp-content/uploads/2025/04/CAP42-Constituion.pdf (253 pages, Last-Modified 9 Apr 2025): "CONSOLIDATED TO 1 JUNE 2020 - LAWS OF SEYCHELLES - CHAPTER 42 - CONSTITUTION OF THE REPUBLIC OF SEYCHELLES [21st June, 1993]" with the list of the Acts and statutory instruments that amend it, followed (from page 148) by subsidiary legislation printed in the same chapter (court rules, standing orders), which is not stored.',
        ],
        "known": [
            "The Laws of Seychelles consolidation to 1 June 2020; amendments after that date (for example the Eleventh Amendment (No. 2) Act 2024) are not applied. The host is the Electoral Commission, which is not the publisher of the text.",
            "Only pages 1-147 (the Constitution and its Schedules) are read; the subsidiary legislation printed after them is not stored. Amendment notes printed in the text (for example 'Art 112(3) am by s 2(e) of Act 7 of 2011 w.e.f. 12 July 2011') are part of the stored text. Headings are the marginal headings printed above each article number.",
        ],
        "documents": [
            _doc(
                slug="sc-en",
                language="en",
                title="Constitution of the Republic of Seychelles",
                citation_name="Seychelles Constitution",
                structure_extra={
                    "style": "numdot",
                    "last_page": 147,
                    "heading_before": "line",
                    "chapter_regex": "^CHAPTER [IVXL]+ -",
                    "start_regex": "^CHAPTER I - THE REPUBLIC$",
                },
            )
        ],
        "url": "https://ecs.sc/wp-content/uploads/2025/04/CAP42-Constituion.pdf",
        "format": "pdf",
        "common": {
            "adopted_on": "1993-06-21",
            "edition_date": "2020-06-01",
            "edition_label": "Laws of Seychelles, Chapter 42, consolidated to 1 June 2020",
            "events": [
                {
                    "type": "commencement",
                    "date": "1993-06-21",
                    "description": "[21st June, 1993]",
                    "locator": "heading",
                }
            ],
            "authoritative": False,
            "text_origin": "official_consolidation",
        },
        "common_structure": {"unit_kind": "article", "page_numbers": True},
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text; no permission for automated retrieval is stated either",
        "text_note": "the Constitution of Seychelles (Laws of Seychelles, Chapter 42, consolidated to 1 June 2020), copy on the site of the Electoral Commission",
    },
    {
        "id": "ls-gov-constitution",
        "cc": "LS",
        "host": "www.gov.ls",
        "short": "Government of Lesotho",
        "publisher": "Government of Lesotho (www.gov.ls), Important Documents: Lesotho Constitution",
        "licence_id": "LS-official-text-no-terms",
        "licence_url": "https://www.gov.ls/",
        "crawl": 5,
        "findings": [
            "robots.txt - www.gov.ls/robots.txt: an empty file (0 bytes): nothing is disallowed; no Crawl-delay",
            'Terms - page footer: "Copyright © 2026 | Government of Lesotho" - a generic footer; no terms-of-use, licence or disclaimer page is linked from the pages read. The last line of the PDF reads "Government of Lesotho©All Rights Reserved-2000".',
            'The document - the download page https://www.gov.ls/download/lesotho-constitution/ ("Lesotho Constitution", 747.80 KB, "Published May 20, 2022") serves, through ?wpdmdl=1329, a PDF of 103 pages (created 5 Sep 2006): "Lesotho - Government Constitutional Matters - THE CONSTITUTION OF LESOTHO - Price: 16.00 Maluti", the text of the Constitution as in the Government Gazette Extraordinary of 1993 followed by a NOTE on the Orders by which it came into operation "on 2nd April 1993".',
        ],
        "known": [
            "The text is the original enactment of 1993 (the copy the Government posts as 'Lesotho Constitution'); it includes none of the amendments made since (for example the Eighth Amendment of 2018, according to search results, and the amendments of 2024 under discussion), so it is attested only at its commencement date (2 April 1993) and many provisions of the Constitution in force today differ.",
            "The contents list is skipped; the Orders of 1993 that follow the text (NOTE) are stored as blocks of paragraphs. The running head and the page numbers are removed.",
        ],
        "documents": [
            _doc(
                slug="ls-en",
                language="en",
                title="The Constitution of Lesotho",
                citation_name="Lesotho Constitution",
                structure_extra={
                    "style": "numdot",
                    "unit_kind": "section",
                    "heading": "same_line",
                    "drop_lines": [
                        "^Lesotho - Government Constitutional Matters$",
                        "^Page [0-9]+$",
                        "^LESOTHO$",
                        "^Government Gazette$",
                        "^EXTRAORDINARY$",
                    ],
                    "chapter_regex": "^CHAPTER [IVXL]+",
                    "tail_regex": "^NOTE$",
                    "start_regex": "^CHAPTER I THE KINGDOM AND ITS CONSTITUTION$",
                },
            )
        ],
        "url": "https://www.gov.ls/download/lesotho-constitution/?wpdmdl=1329",
        "format": "pdf",
        "common": {
            "adopted_on": "1993-04-02",
            "edition_date": "1993-04-02",
            "edition_label": "text of the Government Gazette of 1993; the NOTE records that it came into operation as The Constitution of Lesotho on 2nd April 1993; no amendment is included",
            "events": [
                {
                    "type": "commencement",
                    "date": "1993-04-02",
                    "description": "the draft constitution came into operation as The Constitution of Lesotho on 2nd April 1993",
                    "locator": "NOTE",
                }
            ],
            "authoritative": False,
            "text_origin": "official_authentic",
        },
        "common_structure": {},
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text beyond a generic copyright footer; no permission for automated retrieval is stated either",
        "text_note": "the Constitution of Lesotho in the text of 1993 (without later amendments), as posted by the Government of Lesotho",
    },
    {
        "id": "zw-youth-constitution",
        "cc": "ZW",
        "host": "youth.gov.zw",
        "short": "Ministry of Youth of Zimbabwe",
        "publisher": "Ministry of Youth Empowerment, Development and Vocational Training of Zimbabwe (youth.gov.zw), Resources: Constitution of Zimbabwe",
        "licence_id": "ZW-official-text-no-terms",
        "licence_url": "https://youth.gov.zw/",
        "crawl": 5,
        "findings": [
            'robots.txt - youth.gov.zw/robots.txt: "User-agent: *" "Disallow:" (24 bytes): nothing is disallowed; no Crawl-delay',
            'Terms - page footer: "Copyright © 2026 | Designed by Mugonat Systems" - a generic footer; the home page lists "Constitution of Zimbabwe" among its Downloads; no terms-of-use, licence or disclaimer page is linked',
            'The document - https://youth.gov.zw/storage/01JP9R9JD7HC6DAH8M2589A97Y.pdf (113 pages, 1,201,149 bytes, Last-Modified 1 Dec 2025), the Government Printer\'s text (page head "zowa@zol.co.zw"): "ZIMBABWE - ACT - To repeal and substitute the Constitution of Zimbabwe. ENACTED by the President and the Parliament of Zimbabwe. ... This Act may be cited as the Constitution of Zimbabwe Amendment (No. 20) Act, 2013." followed by "SCHEDULE (SECTION 2) CONSTITUTION OF ZIMBABWE".',
        ],
        "known": [
            "The text is the Constitution as enacted by the Constitution of Zimbabwe Amendment (No. 20) Act, 2013 (the Schedule of the Act); amendments made since (Amendment (No. 1) Act 2017 and later ones) are not applied. The Act prints only its year, so 31 December 2013 is used as the edition date. Other hosts of this text (the Parliament, the National Peace and Reconciliation Commission, the Electoral Commission) are blocked (zw-parlzim-constitution).",
            "The number of section 1 is not printed in the text layer: its heading and sentence ('The Republic - Zimbabwe is a unitary, democratic and sovereign republic.') are carried in the chapter heading shown with sections 2 to 7, not as a provision of their own. A few other headings were not recognised (their text stays in the preceding section and the note says so). The Act's own sections 1 to 3 before the Schedule and the contents list are not stored.",
            "The six Schedules of the Constitution are stored as blocks of paragraphs.",
        ],
        "documents": [
            _doc(
                slug="zw-en",
                language="en",
                title="The Constitution of Zimbabwe",
                citation_name="Zimbabwe Constitution",
                structure_extra={
                    "style": "numspace",
                    "unit_kind": "section",
                    "heading": "same_line",
                    "start_regex": "^PREAMBLE$",
                    "chapter_regex": "^CHAPTER [0-9]+$",
                    "drop_lines": ["^zowa@zol\\.co\\.zw$"],
                },
            )
        ],
        "url": "https://youth.gov.zw/storage/01JP9R9JD7HC6DAH8M2589A97Y.pdf",
        "format": "pdf",
        "common": {
            "edition_date": "2013-12-31",
            "edition_label": "Constitution of Zimbabwe Amendment (No. 20) Act, 2013, Schedule (only the year is printed: 31 December 2013 is used); no later amendment is included",
            "authoritative": False,
            "text_origin": "official_authentic",
        },
        "common_structure": {"page_numbers": True},
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text beyond a generic copyright footer; no permission for automated retrieval is stated either",
        "text_note": "the Constitution of Zimbabwe as enacted in 2013 (the Schedule of the Amendment (No. 20) Act), as posted by the Ministry of Youth",
    },
    {
        "id": "mz-minec-constitution",
        "cc": "MZ",
        "host": "minec.gov.mz",
        "short": "Ministry of Foreign Affairs and Cooperation of Mozambique",
        "publisher": "Ministério dos Negócios Estrangeiros e Cooperação de Moçambique (minec.gov.mz), Documentos: Legislação",
        "licence_id": "MZ-official-text-no-terms",
        "licence_url": "https://minec.gov.mz/",
        "crawl": 5,
        "findings": [
            'robots.txt - minec.gov.mz/robots.txt: "User-agent: *" "Disallow: /wp-admin/" "Allow: /wp-admin/admin-ajax.php" and a sitemap line; no Crawl-delay; /wp-content/uploads/ is not disallowed',
            'Terms - page footer: "©2026. MINEC – Ministério dos Negocios Estrangeiros e Cooperação Implementado por DotCom" - a generic footer; no terms-of-use, licence or disclaimer page is linked',
            'The document - https://minec.gov.mz/wp-content/uploads/2025/09/Constituicao-da-Republica-de-Mocambique.pdf (97 pages, 209,326 bytes, Last-Modified 15 Feb 2026; the file\'s title is "Constituição de 2004, 19 de Nov..doc"): "CONSTITUIÇÃO DA REPÚBLICA - Preâmbulo" ... "Artigo 306 (Entrada em vigor) A Constituição entra em vigor no dia imediato ao da validação e proclamação dos resultados eleitorais das Eleições Gerais de 2004. Aprovada pela Assembleia da República, aos 16 de Novembro de 2004." The Constitutional Council\'s page (cconstitucional.org.mz/constituicao-da-republica/) lists the later texts: "Lei 26/2007 de 16 de Novembro (altera o artigo 304 da Constituição da República)" and "Constituição da República Mocambicana - BR 2018" (a scan; source mz-cconstitucional-constitution).',
        ],
        "known": [
            "The text is the Constitution as approved on 16 November 2004; it includes neither the amendment of article 304 by Lei 26/2007 nor the revision of 2018 (Boletim da República, 22 June 2018), whose text is published only as a scan (mz-cconstitucional-constitution). The 16 November 2004 is used as the edition date, so the text is attested only at that date; many provisions in force today differ.",
            "The headings of the articles are the parenthesised titles printed under each article number; the running text is read without layout.",
        ],
        "documents": [
            _doc(
                slug="mz-pt",
                language="pt",
                title="Constituição da República de Moçambique",
                citation_name="Mozambique Constitution",
                structure_extra={
                    "keywords": ["Artigo"],
                    "heading_next": "paren",
                    "paragraphs": "dot",
                    "preamble_regex": "^Preâmbulo$",
                    "chapter_regex": "^(Título|TÍTULO|Capítulo|CAPÍTULO) ",
                },
            )
        ],
        "url": "https://minec.gov.mz/wp-content/uploads/2025/09/Constituicao-da-Republica-de-Mocambique.pdf",
        "format": "pdf",
        "common": {
            "adopted_on": "2004-11-16",
            "edition_date": "2004-11-16",
            "edition_label": "the text approved by the Assembleia da República on 16 November 2004; no amendment is included",
            "events": [
                {
                    "type": "adoption",
                    "date": "2004-11-16",
                    "description": "Aprovada pela Assembleia da República, aos 16 de Novembro de 2004.",
                    "locator": "closing formula",
                }
            ],
            "authoritative": False,
            "text_origin": "official_authentic",
        },
        "common_structure": {"unit_kind": "article", "style": "keyword", "page_numbers": True},
        "licence_reading": "AMBER - the publisher states no restriction on reusing the text beyond a generic footer; no permission for automated retrieval is stated either",
        "text_note": "the Constitution of Mozambique as approved in 2004 (without the later revisions), as posted by the Ministry of Foreign Affairs",
    },
]

# --------------------------------------------------------------------------- profiles of the new countries

PROFILES: dict[str, dict[str, Any]] = {
    "RW": {
        "name": "Rwanda",
        "names": {"rw": "Repubulika y'u Rwanda", "fr": "République du Rwanda", "en": "Republic of Rwanda"},
        "languages": ["rw", "en", "fr"],
        "families": [],
        "notes": "To verify - a unitary republic with a mixed legal tradition (Belgian civil-law roots, with common-law influences since the 1990s). Kinyarwanda, English and French are official languages (Constitution art 8; Swahili is also an official language by a later law, to verify).",
        "official_sources": ["rw-minijust-constitution"],
        "examples": ["Rwanda Constitution art. 15", "Rwanda Constitution art. 1"],
        "gap": "the Constitution in Kinyarwanda, English and French (Official Gazette n° Special of 04/08/2023)",
        "doc": "constitutions-africa-east-south-deep.md",
    },
    "MG": {
        "name": "Madagascar",
        "names": {"mg": "Repoblikan'i Madagasikara", "fr": "République de Madagascar", "en": "Republic of Madagascar"},
        "languages": ["mg", "fr"],
        "families": ["civil_law"],
        "notes": "To verify - a unitary republic with French-derived civil law and customary rules; Malagasy is the national language and Malagasy and French are the official languages (Constitution art 4, 2010 text).",
        "official_sources": ["mg-assemblee-constitution"],
        "examples": ["Madagascar Constitution art. 8", "Madagascar Constitution art. 1"],
        "gap": "the Constitution of the Fourth Republic (referendum of 17 November 2010) in French and Malagasy",
    },
    "DJ": {
        "name": "Djibouti",
        "names": {"fr": "République de Djibouti", "ar": "جمهورية جيبوتي", "en": "Republic of Djibouti"},
        "languages": ["ar", "fr"],
        "families": [],
        "notes": "To verify - a unitary republic; Arabic and French are the official languages (Constitution art 1); a civil-law system of French inspiration with Islamic and customary rules in personal status.",
        "official_sources": ["dj-presidence-constitution"],
        "examples": ["Djibouti Constitution art. 1", "Djibouti Constitution art. 10"],
        "gap": "the French text of the Constitution ('version 2010')",
    },
    "AO": {
        "name": "Angola",
        "names": {"pt": "República de Angola", "en": "Republic of Angola"},
        "languages": ["pt"],
        "families": ["civil_law"],
        "notes": "To verify - a unitary republic with a Portuguese-derived civil-law system; Portuguese is the official language (Constitution art 19).",
        "official_sources": ["ao-tribunal-constitucional-constitution"],
        "examples": ["Angola Constitution art. 1", "Angola Constitution art. 23"],
        "gap": "the Portuguese text of the Constitution (special updated edition of 2022 of the Tribunal Constitucional)",
    },
    "SO": {
        "name": "Somalia",
        "names": {"so": "Jamhuuriyadda Federaalka Soomaaliya", "en": "Federal Republic of Somalia"},
        "languages": ["so", "ar"],
        "families": ["mixed"],
        "structure": "federal",
        "notes": "To verify - a federal republic; Somali and Arabic are the official languages (Provisional Constitution 2012 art 5; the 2026 text to verify). A mixed system of statutory law, Islamic law (Shari'a) and customary law (xeer).",
        "official_sources": ["so-senate-constitution"],
        "examples": ["Somalia Constitution art. 1", "Somalia Constitution art. 2"],
        "gap": "the Somali text of the Constitution of 2026 (no English or Arabic official text found)",
    },
    "TZ": {
        "name": "Tanzania",
        "names": {"sw": "Jamhuri ya Muungano wa Tanzania", "en": "United Republic of Tanzania"},
        "languages": ["sw", "en"],
        "families": ["common_law"],
        "notes": "To verify - a union of Mainland Tanzania and Zanzibar, which has its own constitution, government and courts for non-union matters; Kiswahili and English are the languages of the Constitution (the Swahili text is authentic). A common-law system with customary and Islamic personal law.",
        "official_sources": ["tz-mocla-constitution", "tz-nao-constitution"],
        "examples": ["Tanzania Constitution art. 12", "Tanzania Constitution art. 1"],
        "gap": "the Constitution in Swahili (consolidated to 30 April 2000) and in the official English revised edition of 2005; both predate later amendments",
    },
    "NA": {
        "name": "Namibia",
        "names": {"en": "Republic of Namibia"},
        "languages": ["en"],
        "families": ["mixed"],
        "notes": "To verify - a unitary republic whose law is Roman-Dutch common law with statute and customary law (Constitution art 66); English is the official language (art 3).",
        "official_sources": ["na-ombudsman-constitution"],
        "examples": ["Namibia Constitution art. 10", "Namibia Constitution art. 1"],
        "gap": "the English text of the Constitution as compiled by the Office of the Ombudsman in February 2016",
    },
    "LS": {
        "name": "Lesotho",
        "names": {"en": "Kingdom of Lesotho", "st": "Mmuso oa Lesotho"},
        "languages": ["en", "st"],
        "families": ["mixed"],
        "notes": "To verify - a unitary constitutional monarchy whose law is Roman-Dutch common law with statute and customary law (Basotho law); Sesotho and English are the official languages (Constitution s 3).",
        "official_sources": ["ls-gov-constitution"],
        "examples": ["Lesotho Constitution s. 4", "Lesotho Constitution s. 1"],
        "gap": "the English text of the Constitution as enacted in 1993, without any later amendment",
    },
    "ZW": {
        "name": "Zimbabwe",
        "names": {"en": "Republic of Zimbabwe"},
        "languages": ["en", "sn", "nd"],
        "families": ["mixed"],
        "notes": "To verify - a unitary republic whose law is Roman-Dutch common law with statute and customary law; English, Shona, Ndebele and thirteen other languages are official (Constitution s 6).",
        "official_sources": ["zw-youth-constitution"],
        "examples": ["Zimbabwe Constitution s. 56", "Zimbabwe Constitution s. 2"],
        "gap": "the English text of the Constitution as enacted by the Amendment (No. 20) Act of 2013, without later amendments",
    },
    "MZ": {
        "name": "Mozambique",
        "names": {"pt": "República de Moçambique", "en": "Republic of Mozambique"},
        "languages": ["pt"],
        "families": ["civil_law"],
        "notes": "To verify - a unitary republic with a Portuguese-derived civil-law system; Portuguese is the official language (Constitution art 10).",
        "official_sources": ["mz-minec-constitution"],
        "examples": ["Mozambique Constitution art. 1", "Mozambique Constitution art. 35"],
        "gap": "the Portuguese text of the Constitution as approved in 2004, without the revisions of 2007 and 2018",
    },
    "SC": {
        "name": "Seychelles",
        "names": {"en": "Republic of Seychelles", "fr": "République des Seychelles", "crs": "Repiblik Sesel"},
        "languages": ["crs", "en", "fr"],
        "families": ["mixed"],
        "notes": "To verify - a unitary republic with a mixed legal system (French-derived civil law and English-derived common law). Seychellois Creole, English and French are the national languages (Constitution art 4).",
        "official_sources": ["sc-ecs-constitution"],
        "examples": ["Seychelles Constitution art. 15", "Seychelles Constitution art. 1"],
        "gap": "the English text of the Constitution (Laws of Seychelles, Chapter 42, consolidated to 1 June 2020)",
    },
}


# --------------------------------------------------------------------------- publishers that could not be loaded

BLOCKED: list[dict[str, Any]] = [
    {
        "id": "et-fsc-constitution",
        "status": "planned",
        "cc": "ET",
        "name": "Constitution of Ethiopia (1995) - Federal Supreme Court of Ethiopia (www.fsc.gov.et)",
        "publisher": "Federal Supreme Court of Ethiopia (www.fsc.gov.et), Digital Law Library",
        "languages": ["am", "en"],
        "url": "https://www.fsc.gov.et/Digital-Law-Library/Federal-Laws/",
        "finding": "robots.txt (www.fsc.gov.et/robots.txt) disallows only technical paths (/admin/, /bin/, /Config/, /Documentation/ ...) and sets no Crawl-delay. Terms (https://www.fsc.gov.et/Terms): 'COPYRIGHT AND TRADEMARK NOTICES: All contents of the Federal Supreme Court of Ethiopia Web Site are: Copyright © The Federal Supreme Court of Ethiopia- All rights reserved and/or its suppliers. ... Any rights not expressly granted herein are reserved.' The Constitution page of the Digital Law Library offers DocumentDownload.ashx?portalid=0&moduleid=1179&articleid=152&documentid=103: a 38-page PDF of the Federal Negarit Gazeta No. 1 of 21 August 1995 (Proclamation No. 1/1995) that is a scan with an OCR text layer: the Amharic columns come out as garbage and the English column has recognition errors and run-together headings ('FUNDAMENTALPIDNCWLESOFTHE'), so the exact text cannot be stored. The Ministry of Justice (justice.gov.et) did not answer: the request for its robots.txt timed out; ethiopia.gov.et answered 'Tunnel connection failed: 502 Bad Gateway'.",
        "limitation": "Scan with an OCR text layer (errors in the English column, garbage in the Amharic column); ULIS does no OCR and does not store text it cannot show exactly.",
        "doc": "Federal Negarit Gazeta No. 1 of 21 August 1995 (scan)",
    },
    {
        "id": "ug-parliament-constitution",
        "status": "planned",
        "cc": "UG",
        "name": "Constitution of Uganda (1995) - Parliament of Uganda (www.parliament.go.ug)",
        "publisher": "Parliament of Uganda (www.parliament.go.ug)",
        "languages": ["en"],
        "url": "https://www.parliament.go.ug/page/constitution",
        "finding": "robots.txt (www.parliament.go.ug/robots.txt) is the standard Drupal file and the page /page/constitution answers HTTP 200 but links no constitution document; the document URL a search engine gave (/documents/1240/constitution) answers HTTP 404. The earlier sweep (docs/constitutions-africa-asia-oceania.md) found that PDF to be a 2007 print of the 1995 text (192 pages), not the consolidated text with the amendments of 2005 and 2017; it was not fetched again. The 7th Revised Edition of the Laws of Uganda (Volume I holds the Constitution) is announced by the Uganda Law Reform Commission, whose host does not verify (ulrc.go.ug: 'unable to get local issuer certificate'); the State House page (statehouse.go.ug/the-constitution/) only links to the Parliament's document. Verification of TLS certificates is never disabled.",
        "limitation": "The only openly reachable official copy predates the amendments of 2005 and 2017; the current consolidation sits on a host whose TLS certificate cannot be verified.",
        "doc": "2007 print of the 1995 text",
    },
    {
        "id": "zm-parliament-constitution",
        "status": "planned",
        "cc": "ZM",
        "name": "Constitution of Zambia - National Assembly of Zambia (www.parliament.gov.zm)",
        "publisher": "National Assembly of Zambia (www.parliament.gov.zm); constitution website of the Government (www.constitution.gov.zm)",
        "languages": ["en"],
        "url": "https://www.parliament.gov.zm/",
        "finding": "The request for robots.txt of www.parliament.gov.zm failed TLS verification ('certificate verify failed: unable to get local issuer certificate'); www.constitution.gov.zm (which publishes the Constitution of Zambia (Amendment) Act No. 13 of 2025) answered the request for robots.txt with 'Connection reset by peer'. Nothing else was requested and certificate verification is never disabled.",
        "limitation": "Both official hosts are unreachable for a client that verifies certificates; the copies on other hosts are Laws.Africa/ZambiaLII (rejected), a Constitute Project compilation (rejected) and ministry copies of the amending Acts only.",
        "doc": "Constitution of Zambia Act, 2016 and its amendments",
    },
    {
        "id": "zw-parlzim-constitution",
        "status": "planned",
        "cc": "ZW",
        "name": "Constitution of Zimbabwe (2013) - Parliament of Zimbabwe (www.parlzim.gov.zw)",
        "publisher": "Parliament of Zimbabwe (www.parlzim.gov.zw); National Peace and Reconciliation Commission (www.nprc.org.zw); Zimbabwe Electoral Commission (www.zec.org.zw)",
        "languages": ["en"],
        "url": "https://www.parlzim.gov.zw/",
        "finding": "www.parlzim.gov.zw/robots.txt answered with rules (a group disallowing /parlzim/ and a Yoast group with an empty Disallow), but the request for the home page then answered HTTP 403 to the ULIS agent: the host refuses it and nothing more was requested. www.nprc.org.zw (which hosts a PDF of the Constitution) failed TLS verification ('certificate verify failed: Hostname mismatch, certificate is not valid for www.nprc.org.zw'); www.zec.org.zw answered 'Connection reset by peer'. No workaround was tried.",
        "limitation": "The hosts of the official copies refuse the agent or fail certificate verification; Veritas, ZimLII (Laws.Africa) and Refworld copies are not official.",
        "doc": "Constitution of Zimbabwe Amendment (No. 20) Act, 2013",
    },
    {
        "id": "mz-cconstitucional-constitution",
        "status": "planned",
        "cc": "MZ",
        "name": "Constituição da República de Moçambique (2004, revista em 2018) - Conselho Constitucional (cconstitucional.org.mz)",
        "publisher": "Conselho Constitucional de Moçambique (cconstitucional.org.mz); Assembleia da República (www.parlamento.mz)",
        "languages": ["pt"],
        "url": "https://cconstitucional.org.mz/constituicao-da-republica/",
        "finding": "robots.txt of cconstitucional.org.mz ('User-agent: *' 'Disallow: /wp-admin/') and of www.parlamento.mz (404, none) allow the pages. The PDF of the text revised in 2018 (Boletim da República, I Série, 22 June 2018) on both hosts is the same file (ConstituicaodaRepublicaMocambicana-BR2018.pdf, 14,139,003 bytes, 40 pages) and has no text layer (pdftotext returns 40 characters for 40 pages): it is a scan. The PDF with a text layer found on another official host (minec.gov.mz, 97 pages, file title 'Constituição de 2004, 19 de Nov.') carries the text of 2004 without the revisions of 2007 and 2018.",
        "limitation": "The current text exists only as a scan; the text-layer copies are superseded. ULIS does no OCR.",
        "doc": "Boletim da República of 22 June 2018 (scan)",
    },
    {
        "id": "bi-presidence-constitution",
        "status": "planned",
        "cc": "BI",
        "name": "Constitution de la République du Burundi (2018) - Présidence de la République (presidence.gov.bi)",
        "publisher": "Présidence de la République du Burundi (presidence.gov.bi)",
        "languages": ["fr"],
        "url": "https://presidence.gov.bi/le-burundi/constitution/",
        "finding": "robots.txt (presidence.gov.bi/robots.txt, Yoast: 'User-agent: *' 'Disallow:') allows everything; page footer '© 2026 Ntare House'; no terms page. The page embeds https://presidence.gov.bi/wp-content/uploads/2018/07/constitution-promulguee-le-7-juin-2018-1.pdf (4,686,702 bytes, 59 pages), a scan with no text layer (pdftotext returns 59 characters for 59 pages).",
        "limitation": "The official text of 7 June 2018 exists only as a scan; ULIS does no OCR.",
        "doc": "constitution promulguée le 7 juin 2018 (scan)",
    },
    {
        "id": "km-coursupreme-constitution",
        "status": "planned",
        "cc": "KM",
        "name": "Constitution de l'Union des Comores - Cour suprême (www.coursupremecomores.km)",
        "publisher": "Cour suprême de l'Union des Comores (www.coursupremecomores.km); Cour constitutionnelle (ccuc.km)",
        "languages": ["fr"],
        "url": "https://www.coursupremecomores.km/telecharge/constitution-des-comores/",
        "finding": "robots.txt (www.coursupremecomores.km/robots.txt) disallows only /wp-admin/, /wp-includes/ (except js and images), /trackback/ and the login pages; footer 'Cour suprême de l'Union des Comores © 2025 | Designed By CBon Business Solutions'. The download of 'Constitution 2021' (/wp-content/uploads/2025/08/Constitution-2021-.pdf, 24,087,180 bytes, 36 pages) is a scan with no text layer (36 characters for 36 pages). The Constitutional Court's host (ccuc.km) answered 'Tunnel connection failed: 502 Bad Gateway'.",
        "limitation": "The only official copy found is a scan; ULIS does no OCR.",
        "doc": "Constitution 2021 (scan)",
    },
    {
        "id": "ss-mojca-constitution",
        "status": "planned",
        "cc": "SS",
        "name": "Transitional Constitution of South Sudan (2011) - Ministry of Justice and Constitutional Affairs (mojca.gov.ss)",
        "publisher": "Ministry of Justice and Constitutional Affairs (mojca.gov.ss); Ministry of Foreign Affairs (mofaic.gov.ss); National Elections Commission (nec.gov.ss)",
        "languages": ["en"],
        "url": "https://mojca.gov.ss/laws-of-the-republic-of-south-sudan/",
        "finding": "The requests for robots.txt of mojca.gov.ss and mofaic.gov.ss failed TLS verification ('certificate verify failed: certificate has expired'); certificate verification is never disabled. The National Elections Commission (nec.gov.ss; robots.txt allows everything but /wp-admin/) offers 'Constitution 2011 with 2013 amendments', a 69-page PDF that is the Constitute Project compilation ('PDF generated: 27 Apr 2022 ... constituteproject.org ... Subsequently amended'); Constitute Project is a rejected source (constitute-project) and its file is not used.",
        "limitation": "The official hosts have an expired certificate; the only reachable copy is a third-party compilation.",
        "doc": "Transitional Constitution 2011 (amended 2013)",
    },
    {
        "id": "sz-gov-constitution",
        "status": "planned",
        "cc": "SZ",
        "name": "Constitution of the Kingdom of Eswatini (2005) - Government of Eswatini (www.gov.sz)",
        "publisher": "Government of the Kingdom of Eswatini (www.gov.sz); Parliament of Eswatini (parliament.gov.sz)",
        "languages": ["en"],
        "url": "https://www.gov.sz/index.php/resources/constitution",
        "finding": "The requests for robots.txt of www.gov.sz (which hosts /images/Media/constitution.pdf, the Constitution of the Kingdom of Swaziland Act 2005) and of parliament.gov.sz both ended in 'Connection reset by peer'; nothing else was requested.",
        "limitation": "Both official hosts reset the connection; copies on other hosts are Laws.Africa and Constitute Project compilations (rejected).",
        "doc": "Constitution of the Kingdom of Swaziland Act 2005",
    },
    {
        "id": "mw-justice-constitution",
        "status": "planned",
        "cc": "MW",
        "name": "Constitution of Malawi (1994) - Ministry of Justice (www.justice.gov.mw)",
        "publisher": "Ministry of Justice of Malawi (www.justice.gov.mw)",
        "languages": ["en"],
        "url": "https://www.justice.gov.mw/sites/default/files/2021-06/Malawi%20Constitution.pdf",
        "finding": "robots.txt (www.justice.gov.mw/robots.txt: 'User-agent: *' 'Allow: /') allows everything; the Disclaimer (/index.php/ministry/disclaimer) says the information is provided 'as is' and says nothing about reuse; footer '© Ministry of Justice'. The PDF (93 pages, uploaded 25 Jun 2021) has the file title 'Constitution of the Republic of Malawi (as amended up to Act No. 38 of 1998)' and its text layer lacks the section numbers (the first subsection of every section comes out as '1.'), so the structure cannot be recognised; amendments after 1998 are not in it.",
        "limitation": "Consolidated only to 1998 and with the section numbers missing from the text layer.",
        "doc": "Constitution of the Republic of Malawi (as amended up to Act No. 38 of 1998)",
    },
    {
        "id": "bw-parliament-constitution",
        "status": "rejected",
        "cc": "BW",
        "name": "Constitution of Botswana - Parliament of Botswana (www.parliament.gov.bw)",
        "publisher": "Parliament of Botswana (www.parliament.gov.bw)",
        "languages": ["en"],
        "url": "https://www.parliament.gov.bw/images/constitution.pdf",
        "finding": "robots.txt (www.parliament.gov.bw/robots.txt, Joomla) does not disallow /images/. The PDF (59 pages, Last-Modified 14 Nov 2022, created 11 Jun 2008) prints the line 'Copyright Government of Botswana' on every page (59 times) and states no licence or permission; the Botswana e-Laws host (elaws.gov.bw) answered 'Tunnel connection failed: 502 Bad Gateway'.",
        "limitation": "A copyright notice on every page of the text with no permission; the copy is also of 2008.",
        "doc": "CONSTITUTION OF BOTSWANA (PDF of 2008)",
    },
]

# --------------------------------------------------------------------------- writers


def _wrap(text: str, indent: str = "#   ", width: int = 124) -> str:
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent + "  ")


def _dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=118, default_flow_style=False)


def source_yaml(p: dict[str, Any]) -> str:
    docs = p["documents"]
    langs = sorted({d["language"] for d in docs}, key=lambda x: [d["language"] for d in docs].index(x))
    n_docs = len(docs)
    lang_list = ", ".join(langs)
    requests = n_docs + 1
    findings = [f"Read on {READ_ON}, before any document was fetched:"] + p["findings"]
    header = f"# {p['publisher']}. {findings[0]}\n" + "\n".join(_wrap(f) for f in findings[1:])
    reviewed = (
        f"ULIS project (not counsel), {READ_ON}, from the publisher's own pages quoted in the header of this file: "
        + " ".join(f"{f.rstrip('.')}." for f in p["findings"])
        + f" Reading: {p['licence_reading']}. The text is an official legal text; the national copyright law is believed not to protect "
        "official texts of this kind (to verify; not read on a publisher's page)."
    )
    licence_name = p.get(
        "licence_name",
        f"Official legal text published by {p['short']}; no terms of reuse stated ({p['licence_reading'].split(' - ')[0]})",
    )
    data: dict[str, Any] = {
        "schema_version": 1,
        "id": p["id"],
        "name": f"Constitution of {_country(p['cc'])} - {p['short']} ({p['host']})",
        "publisher": p["publisher"],
        "kind": "official_legislation_portal",
        "authority": "official_non_authentic",
        "jurisdictions": [p["cc"]],
        "document_kinds": ["constitution"],
        "languages": langs,
        "coverage": f"The Constitution of {_country(p['cc'])} in {lang_list} as published by {p['short']}: {n_docs} document(s). "
        + p["text_note"][0].upper()
        + p["text_note"][1:]
        + ".",
        "access": {
            "method": "permitted_web_retrieval",
            "endpoint": f"config/manifests/{p['id']}.yaml",
            "authentication": "none",
        },
        "update_frequency": "Not stated; the retrieval date is the edition date unless the document prints one.",
        "rate_limits": f"As recorded under robots.txt in the header; ULIS waits at least {p['crawl']} s between requests, identifies itself, caches every download and makes {requests} request(s) (robots.txt and {n_docs} document(s)).",
        "licence": {
            "id": p["licence_id"],
            "name": licence_name,
            "url": p["licence_url"],
            "review_status": "approved",
            "reviewed_by": reviewed,
            "reviewed_on": READ_ON,
            "redistribution": p.get("redistribution", "allowed_with_conditions"),
            "bulk_download": "allowed_with_conditions",
            "automated_processing": "allowed_with_conditions",
            "commercial_use": "unknown",
            "attribution_required": True,
            "attribution_text": f"Constitution of {_country(p['cc'])}, {p['short']} ({p['host']}); retrieved {{retrieved_date}}. Structured and indexed by ULIS; not the authentic text and not endorsed by the publisher.",
            "conditions": [
                "The text is the publisher's copy, not the authentic publication; amendments after the edition date are not applied.",
                f"Polite retrieval (identifying User-Agent, {p['crawl']} s between requests, cached downloads); stop on any refusal.",
            ],
            "notes": "The basis is the official status of the text and the absence of any stated restriction on it (or, for Mauritius, the State's copyright notice); there is no other licence. Counsel review is recommended before production use.",
        },
        "provenance": f"The document(s) listed in config/manifests/{p['id']}.yaml, stored byte for byte inside JSON bundles with the URL and retrieval time; the text is re-derived from the page or from the PDF's text layer (pdftotext).",
        "known_limitations": p["known"],
        "status": "active",
        "connector": "constitution_docs",
        "parser": "constitution_text",
    }
    return header + "\n" + _dump(data)


def _country(cc: str) -> str:
    return {
        "RW": "Rwanda",
        "MG": "Madagascar",
        "MU": "Mauritius",
        "DJ": "Djibouti",
        "AO": "Angola",
        "SO": "Somalia",
        "TZ": "Tanzania",
        "NA": "Namibia",
        "SC": "Seychelles",
        "LS": "Lesotho",
        "ZW": "Zimbabwe",
        "MZ": "Mozambique",
    }[cc]


def manifest_yaml(p: dict[str, Any]) -> str:
    docs = []
    for d in p["documents"]:
        structure = {**p["common_structure"], **d.get("structure_extra", {})}
        if "crop" in d:
            structure["crop"] = d["crop"]
        entry: dict[str, Any] = {
            "slug": d["slug"],
            "jurisdiction": p["cc"],
            "url": p["url"],
            "format": p["format"],
            "language": d["language"],
            "title": d["title"],
            "citation_name": d["citation_name"],
        }
        for key in ("adopted_on", "edition_date", "edition_label", "events", "authoritative", "text_origin"):
            if key in p["common"]:
                entry[key] = p["common"][key]
        entry["structure"] = structure
        docs.append(entry)
    manifest = {
        "schema_version": 1,
        "source_id": p["id"],
        "user_agent": UA,
        "crawl_delay_seconds": p["crawl"],
        "hosts": [p["host"]],
        "documents": docs,
    }
    head = f"# The Constitution of {_country(p['cc'])} ({', '.join(d['language'] for d in docs)}): {len(docs)} document(s) on {p['host']}. See config/sources/{p['id']}.yaml.\n"
    return head + _dump(manifest)


def profile_yaml(cc: str, spec: dict[str, Any]) -> str:
    sources = spec["official_sources"]
    data = {
        "schema_version": 1,
        "id": cc,
        "profile_version": "0.1.0",
        "name": spec["name"],
        "names": spec["names"],
        "kind": "sovereign_state",
        "parent": "INT",
        "fictional": False,
        "verification": {"status": "draft_unverified", "notes": "Research notes only. Not reviewed by counsel."},
        "official_languages": spec["languages"],
        "legal_system": {
            "families": spec["families"],
            "structure": spec.get("structure", "unitary"),
            "treaty_reception": "unknown",
            "notes": spec["notes"],
        },
        "legislative_institutions": [],
        "instrument_types": ["constitution"],
        "norm_hierarchy": ["constitution"],
        "courts": [],
        "precedent": {
            "doctrine": "not_applicable",
            "vertical_binding": False,
            "notes": "Not profiled: only the constitution is loaded, no court decisions. To verify - how precedent works in this jurisdiction.",
        },
        "citation": {
            "module": "constitution",
            "notes": f'The constitution is cited as "{spec["name"]} Constitution art. N" (a label, with the article number as the document itself prints it). To verify - the country\'s own citation convention.',
            "examples": spec["examples"],
        },
        "publication": {"official_sources": sources},
        "coverage": {
            "corpus_status": "pilot",
            "known_gaps": [
                f"Only the constitution is loaded ({spec['gap']}). No legislation, regulations, treaties or case law of {spec['name']} are ingested.",
                "The constitution is stored as the text of the publisher's page or PDF at the retrieval date; amendments adopted since the edition date are not applied, and no history is held (docs/constitutions-africa-east-south-deep.md).",
                "Courts, legislative institutions, legal families and treaty reception are not profiled: this is a minimal profile, not reviewed by counsel.",
            ],
        },
    }
    head = (
        f"# REAL JURISDICTION - DRAFT, UNVERIFIED. {spec['name']}: a minimal profile created for the constitutions sweep of East and\n"
        "# Southern Africa. Only the constitution is loaded; nothing else about the legal system is profiled.\n"
    )
    return head + _dump(data)


def blocked_yaml(b: dict[str, Any]) -> str:
    status = b["status"]
    rejected = status == "rejected"
    head = (
        f"# {status.upper()} - evaluated {READ_ON}, not ingested. Findings are in licence.reviewed_by (publisher's own pages, robots.txt first).\n"
        f"# Identifying User-Agent used for every request: {UA}\n"
    )
    data: dict[str, Any] = {
        "schema_version": 1,
        "id": b["id"],
        "name": b["name"],
        "publisher": b["publisher"],
        "kind": "official_legislation_portal",
        "authority": "official_non_authentic",
        "jurisdictions": [b["cc"]],
        "document_kinds": ["constitution"],
        "languages": b["languages"],
        "coverage": f"The Constitution ({b['doc']}) on the publisher's site; not ingested.",
        "access": {"method": "permitted_web_retrieval", "endpoint": b["url"], "authentication": "none"},
        "update_frequency": "unknown",
        "licence": {
            "id": "UNKNOWN",
            "name": "Not usable: " + b["limitation"],
            "url": b["url"],
            "review_status": "rejected" if rejected else "pending_review",
            "reviewed_by": f"ULIS project (not counsel), {READ_ON}, from the publisher's own pages and robots.txt. Findings: "
            + b["finding"],
            "reviewed_on": READ_ON,
            "redistribution": "not_allowed" if rejected else "unknown",
            "bulk_download": "not_allowed" if rejected else "unknown",
            "automated_processing": "not_allowed" if rejected else "unknown",
            "commercial_use": "not_allowed" if rejected else "unknown",
            "attribution_required": None,
            "attribution_text": None,
        },
        "provenance": "Evaluated and NOT ingested by the constitutions sweep of East and Southern Africa (no document was stored). See the licence block and docs/constitutions-africa-east-south-deep.md.",
        "known_limitations": [b["limitation"]],
        "status": status,
        "connector": None,
        "parser": None,
    }
    return head + _dump(data)


def main() -> None:
    for p in PUBLISHERS:
        (ROOT / "config/sources" / f"{p['id']}.yaml").write_text(source_yaml(p), encoding="utf-8")
        (ROOT / "config/manifests" / f"{p['id']}.yaml").write_text(manifest_yaml(p), encoding="utf-8")
    for b in BLOCKED:
        (ROOT / "config/sources" / f"{b['id']}.yaml").write_text(blocked_yaml(b), encoding="utf-8")
    for cc, spec in PROFILES.items():
        (ROOT / "config/jurisdictions" / f"{cc.lower()}.yaml").write_text(profile_yaml(cc, spec), encoding="utf-8")
    print(
        f"wrote {len(PUBLISHERS)} sources and manifests, {len(BLOCKED)} blocked descriptors, {len(PROFILES)} profiles"
    )


if __name__ == "__main__":
    main()
