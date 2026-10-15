# Sejm ELI API fixtures (Poland)

Excerpts of real responses of the Sejm's ELI API (https://api.sejm.gov.pl/eli), the machine interface to the Dziennik Ustaw,
retrieved on 2026-10-14 with the project's polite fetcher (identifying User-Agent, 5 s between requests); `meta.json` has each
document's URL and retrieval time. They were fetched under the terms recorded in `config/sources/pl-eli-sejm.yaml`: no robots.txt
on `api.sejm.gov.pl` (404), no terms of use in the API documentation, and Prawo autorskie art. 4 pts 1-2 (normative acts and official
documents are not protected by copyright).

Each file is the JSON envelope the connector stores (`ulis-pl-eli-bundle/1`):

* `*_<year>_<pos>.json` (`kind: consolidated_text`): the announcement's API record (`tj_record`, verbatim), a few stable facts about
  the act (`act`, copied from its record) and `text_html`, an excerpt of the HTML the API serves at `.../text.html`. In the excerpt
  the announcement's own text (part 1), the table of contents and the footnote list are left out; the heading of the annex and, of
  the annex, the divisions needed to reach the kept articles and the kept articles themselves are byte-for-byte as served (nothing
  inside an article is altered; most articles are left out).
* `*_references.json` (`kind: change_report`): the API record of the act itself, verbatim, with the consolidated text it attaches to.

| file | text (Dz.U.) | legal status | articles kept |
|---|---|---|---|
| `kc_2023_1610.json` | Kodeks cywilny, tekst jednolity 2023 poz. 1610 | 2023-07-28 | 2, 22¹, 24, 172, 263, 415, 417 |
| `kc_2024_1061.json` | Kodeks cywilny, tekst jednolity 2024 poz. 1061 | 2024-06-19 | the same (art. 24 § 1 and art. 172 § 3 changed between the two) |
| `kc_references.json` | Kodeks cywilny (1964 nr 16 poz. 93) | attached to 2024-06-19 | - |
| `kk_2024_17.json` | Kodeks karny, tekst jednolity 2024 poz. 17 | 2023-12-05 | 1, 148 |
| `uodo_2019_1781.json` | ustawa o ochronie danych osobowych, tekst jednolity 2019 poz. 1781 | 2019-08-29 | 1, 35 (a quoted oath) |
| `uodo_references.json` | ustawa o ochronie danych osobowych (2018 poz. 1000) | attached to 2019-08-29 | - |
| `kp_2023_1465.json` | Kodeks pracy, tekst jednolity 2023 poz. 1465 | 2023-06-13 | 18³ᵃ, 52, 266-280, 283 and its stray "Rozdział II", Dział TRZYNASTY and CZTERNASTYᵃ notes; "Preambuła (uchylona)" |
| `kpa_2023_775.json`, `kpa_2024_572.json` | Kodeks postępowania administracyjnego, 2023 poz. 775 / 2024 poz. 572 | 2023-04-13 / 2024-03-27 | 2a, 7, 164-179 |
| `kpa_references.json` | Kodeks postępowania administracyjnego (1960 nr 30 poz. 168) | attached to 2024-03-27 | - |
| `kro_2023_2809.json` | Kodeks rodzinny i opiekuńczy, tekst jednolity 2023 poz. 2809 | 2023-12-06 | 23, 99²ᵃ |
| `forms_2024_1646.json` | Prawo bankowe (1997 nr 140 poz. 939), tekst jednolity 2024 poz. 1646 | 2024-09-26 | a composite of whole units, byte-for-byte as served, retrieved 2026-10-15 from four consolidated texts so that one file holds the markup of the wider corpus: art. 4 pkt 35 (letters with "- text" lists) and art. 13b (a stray "A. Banki państwowe") and, within the "A. Plany naprawy" division of chapter 12, art. 141m (a labelled division that holds articles) of Dz.U. 2024 poz. 1646; ust. 5 of art. 4 of Dz.U. 2024 poz. 1176 as art. 5 (a table, cut to its headings and two rows); pkt 3 of ust. 2 of art. 8 of Dz.U. 2024 poz. 754 (a formula); the division "Dział III" with its second heading line and art. 273 of Dz.U. 2023 poz. 1170. The unit shells (article numbers and ids) around the borrowed units are written for the test; the units are not altered |

They are test inputs only. The consolidated texts are official announcements; the authentic text of the law is the act and its
amending acts as published in the Dziennik Ustaw.
