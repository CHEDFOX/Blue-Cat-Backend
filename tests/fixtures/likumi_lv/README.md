# Likumi.lv fixtures

Excerpts of five real document pages from https://likumi.lv (official publisher "Latvijas Vēstnesis"),
retrieved 2026-10-14 with the project's polite fetcher (identifying User-Agent, 5 s between requests).
`meta.json` has each page's URL and retrieval time. The pages were fetched under the portal's terms of use
(https://likumi.lv/par-mums/portala-lietosanas-noteikumi: free access, no restriction on reuse) and
Autortiesību likums 6. panta pirmā daļa 1. punkts (legislative acts and their official consolidated versions
are not protected by copyright); see `config/sources/lv-likumi.yaml`.

Each file keeps, byte for byte, the page's title, passport block ("Tiesību akta pase"), the `ver_date` edition
list and the `doc_id` element, and a selection of whole blocks of the document body (`TVnnn` divs); nothing
inside a block is altered, but most blocks of each act are left out:

| file | document (id) | blocks kept |
|---|---|---|
| `satversme.html` | Latvijas Republikas Satversme (57980) | preamble, Art. 1, 2, 68, 69, 81 (repealed), 91 and their chapter headings |
| `kriminallikums.html` | Krimināllikums (88966) | Art. 1, 38.¹, 45 (repealed), 70.⁴, 117, 118, transitional 1, 11, 25.³ (repealed), the EU reference; the edition list has a future edition (15.10.2026) |
| `darba-likums.html` | Darba likums (26019) | Art. 1, 100, 101, transitional 1 and headings |
| `civillikums-1.html` | Civillikums, PIRMĀ DAĻA (90223) | Art. 26-28 and headings |
| `administrativa-procesa-likums.html` | Administratīvā procesa likums (55567) | Art. 1 and 317 (sub-points) |

They are test inputs only; the texts are informative systematisations, the official publication is in Latvijas Vēstnesis.
