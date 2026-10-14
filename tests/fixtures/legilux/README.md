# Legilux fixtures

Akoma Ntoso (CSD13) XML of Luxembourg consolidated legislation, French, from Legilux (Ministère d'État - Service
central de législation), retrieved on 2026-10-14 from `https://legilux.public.lu/filestore/eli/etat/leg/...`
(see `config/sources/lu-legilux.yaml`). Licence: CC BY 4.0 (stated in each file: `jolux:license`).
Consolidated texts have no legal value; the Mémorial A is authentic. Stored gzip-compressed.

| File | Consolidation | Content |
|---|---|---|
| `constitution-20090101.xml.gz` | Constitution, applicable 2009-01-01 (ends 2009-03-15) | whole file |
| `constitution-20090316.xml.gz` | Constitution, applicable 2009-03-16 | whole file; Art. 34 differs from the file above |
| `loi-2018-08-01-a686-20251226.xml.gz` | Loi du 1er août 2018 (CNPD, protection des données), applicable 2025-12-26 | whole file |
| `code-civil-20250420-excerpt.xml.gz` | Code civil, applicable 2025-04-20 | **excerpt**: nine articles (`art_1er`, `art_12`, `art_34`, `art_59`, `art_63`, `art_86`, `art_359`, `art_1165`, `art_1382`) with their book/title/chapter wrappers; all other articles and the subject index removed |
| `code-civil-20251226-excerpt.xml.gz` | Code civil, applicable 2025-12-26 | the same excerpt; `art_59`, `art_86`, `art_359`, `art_1165` have changed wording |
| `code-travail-20260726-excerpt.xml.gz` | Code du travail, applicable 2026-07-01 | **excerpt**: `art_l_010-1`, `art_l_010-2` |

Excerpts were made by deleting `article` elements (and wrappers left empty) from the downloaded file with
ElementTree; the remaining elements and text are the publisher's, but the bytes are re-serialised, so an excerpt is
not byte-identical to the download. Whole files are the downloaded bytes.
