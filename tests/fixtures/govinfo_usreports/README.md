# GovInfo United States Reports fixtures

Real files from https://www.govinfo.gov, retrieved 2026-10-14 (robots.txt and the policy pages read first; see
`config/sources/us-govinfo-usreports.yaml`). Public domain (17 U.S.C. 105 per GovInfo's notice).

| File | Source | Content |
|---|---|---|
| `robots.txt` | `/robots.txt` | the host's robots.txt, unchanged |
| `vol410-roe.mods.xml` | `/metadata/pkg/USREPORTS-410/mods.xml` | package MODS of volume 410; only the constituent entries of 410 U.S. 113 (Roe v. Wade) and 179 (Doe v. Bolton) are kept, every other CASE entry is removed (byte for byte otherwise) |
| `vol410-roe.txt.gz` | `/content/pkg/USREPORTS-410/text/USREPORTS-410.txt` | contiguous slice of the lines: from just before Roe's first page to the caption of Doe v. Bolton and 12 lines after |
| `vol250-shared.*` | volume 250 (1919 Term) | cases at pages 28, 30 and 33; page 30 is shared by Schlitz Brewing Co. v. Houston Ice Co. and Coleman v. United States |
| `vol550-hinck.mods.xml`, `vol550-hinck.pdftext.gz` | volume 550 (2006 Term), which has no text rendition | MODS with only the entries of pages 501 (Hinck v. United States), 511 (Office of Sen. Mark Dayton v. Hanson) and 516; and the `pdftotext` output (form feed between pages) of 18 pages (printed 500-517) of `/content/pkg/USREPORTS-550/pdf/USREPORTS-550.pdf`. The 9.8 MB PDF slice itself is not kept: the parser test substitutes this real extraction for the extraction step, which `tests/test_govinfo_uscourts.py` tests on real PDFs |
| `vol586-stitt.*` | volume 586 (2018 Term) | cases at pages 27 (United States v. Stitt) and 38; modern layout, the OCR lost the "fi" ligature ("specifc", "defned") |

The slices are cut from the full volume files; nothing inside them is edited. The last listed case of each fixture is
deliberately incomplete (where it ends is not in the slice), so the parser must not ingest it.
