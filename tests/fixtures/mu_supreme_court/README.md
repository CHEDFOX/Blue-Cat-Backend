# Fixtures: decisions from the Supreme Court of Mauritius website

Source: Judgment Search, https://supremecourt.govmu.org/judgment-search (the court's own website). Retrieved
2026-10-14. Terms (https://supremecourt.govmu.org/basic-page/4527, clause 3): the judgments may be reproduced
free of charge in any format or medium if reproduced accurately and not used in a misleading context, with the
source identified and the copyright status acknowledged. Copyright in the judgments is held by the Supreme Court of
Mauritius; the site says its versions are "for convenience of reference only".

NOTE: these files were fetched by hand while the source was being evaluated, before the LAST line of the site's
robots.txt (`Disallow: /`, see `robots.txt` here) had been read correctly. The site is therefore recorded as
`planned` and nothing else was or will be fetched. Delete this directory and the tests that use it if that use is
not acceptable.

| File | What it is |
|---|---|
| `robots.txt` | the site's robots.txt as served on 2026-10-14 |
| `listing-2026-0000.html` | four rows of the listing page, verbatim except the checkbox values (Noorbux, Turquoise, Spice Finance II, Govind), in a minimal page shell with a one-page pager |
| `noorbux.pdf` | the PDF of `2026 LPW 125` (Mohammad Nowsad Noorbux v Police), unchanged (405 KB) |
| `bundle-<node id>.json` | the bundle the connector builds for each of the four rows (listing row, PDF URL/SHA-256/size, `pdftotext 24.02.0` text): 19490869 Noorbux (`2026 LPW 125`, number on its own line), 19490555 Turquoise (`2026 INT 287`, Intermediate Court, numbering glitches, quotation), 19493399 Spice Finance (`2026 PL3 14`, unnumbered, "Page n" footers), 19480656 Govind (`2026 BMB 59`, `[n]` markers) |
