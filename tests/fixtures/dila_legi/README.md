# DILA LEGI fixtures (France)

Four small bundles in the format `ulis/sources/connectors/dila_legi.py` writes (a zip of the publisher's XML files, byte for
byte, plus `bundle.json`). Every `article/`, `section_ta/`, `struct/` and `version/` file is a file of the stock
`Freemium_legi_global_20250713-140000.tar.gz`, taken unchanged from
<https://echanges.dila.gouv.fr/OPENDATA/LEGI/Freemium_legi_global_20250713-140000.tar.gz> (downloaded 2026-10-14, SHA-256
`f862b4a2238e11ec3c48ec70f3b1c68bac0bb1985907841666b98608653d4eca`). Only the files needed for the chosen articles were kept (the two decrees are complete). The decree bundles were built by the connector itself from the stock on 2026-10-18 (`bundle.json`: stock 2025-07-13, no increment applied).

Licence: Licence Ouverte / Open Licence 2.0, as stated by the DILA data sheet for LEGI
(<https://echanges.dila.gouv.fr/OPENDATA/LEGI/DILA_LEGI_Presentation_20170824.pdf>); source: Direction de l'information
légale et administrative (DILA), base LEGI. See `config/sources/fr-dila-legi.yaml`.

| File | Text | Articles kept (all versions) | What it shows |
|---|---|---|---|
| `loi-78-17.zip` | Loi n° 78-17 du 6 janvier 1978 (`JORFTEXT000000886460`) | 1, 4, 22 | sections with dated titles, an article whose wording changed many times, notes with amendment links |
| `decret-2008-1484.zip` | Décret n° 2008-1484 du 22 décembre 2008 relatif aux actes de gestion du patrimoine des personnes placées en curatelle ou en tutelle (`JORFTEXT000020017088`, in force) | all 7 articles and the two annexes (every version: 12 article files, 1 section) | a decree in force; annexes stored as articles (`Annexe 1`, four versions, HTML tables, erratum and commencement notes in `NOTA`); articles citing the Code civil; article 5 only abrogates another decree (TYPE `ENTIEREMENT_MODIF`, no ETAT, no period: recorded without a version); a `TITRE` with a line break |
| `decret-2020-1006.zip` | Décret n° 2020-1006 du 6 août 2020 fixant les valeurs des ratios ... (`JORFTEXT000042219936`, repealed on 2023-05-01) | all 6 articles and the two annexes (9 article files, 2 sections) | a repealed decree (every article ends on 2023-05-01: `repealed` markers); two unnumbered annex articles (empty `NUM`: `art-x`, `art-x-42219958`), one with two versions; article 5 only abrogates another decree |
| `code-civil.zip` | Code civil (`LEGITEXT000006070721`) | 1240, 1100, 1844-11, 445, 2119, 1353, 2313, 1569-1571, 1574 | a number re-used after the 2016 reform (1100, 1240), an article abrogated in 2002, a scheduled change (1844-11: ABROGE_DIFF / VIGUEUR_DIFF on 2025-10-01), a version modified before it came into force (445: MODIFIE_MORT_NE, not ingested), an article transferred out of the code (2119: TRANSFERE) |

The archive-level tests build tiny `.tar.gz` archives in the layout of the DILA stock from these files at test time (and
a synthetic increment whose changes are made up for the test of the overlay mechanism only; it is never ingested as law).
