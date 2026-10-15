# Fixtures: United States state law (Washington, Texas, Oregon, Utah)

Real files retrieved on 2026-10-14 with the ULIS User-Agent through the project connectors (5 s / robots crawl delay);
licence and terms in `config/sources/us-wa-rcw.yaml`, `us-tx-statutes.yaml`, `us-or-ors.yaml` and `us-ut-code.yaml`.
The bundle envelope (URL, retrieval time) is added by the tests; the files hold the bytes as received.

| File | Origin |
|---|---|
| `wa_rcw_9A.32.html.gz` | https://app.leg.wa.gov/rcw/default.aspx?cite=9A.32&full=true (chapter 9A.32 RCW, Homicide), gzip |
| `tx_PE_excerpt.htm.zip` | members `pe.19.htm`, `pe.32.htm`, `pe.46.htm`, `pe.22_old.htm` of https://tcss.legis.texas.gov/resources/Zips/PE.htm.zip (re-zipped, bytes unchanged) |
| `tx_CR_excerpt.htm.zip` | members `cr.2a.htm`, `cr.5.htm`, `CR.102.htm`, `cr.17-old.htm` of `.../Zips/CR.htm.zip` (re-zipped, bytes unchanged) |
| `or_ors012.html.gz`, `or_ors285A.html.gz` | https://www.oregonlegislature.gov/bills_laws/ors/ors012.html and `ors285A.html` (Windows-1252), gzip |
| `ut_title3.xml.gz` | https://le.utah.gov/xcode/Title3/C3_1800010118000101.xml (whole title 3), gzip |
| `ut_title76_excerpt.xml.gz` | https://le.utah.gov/xcode/Title76/C76_2026050620260701.xml cut to the title header, chapter 76-2 and part 76-5-2 (elements byte for byte; the closing tags are added), gzip |
| `ut_code.html.gz`, `ut_code_index.html.gz` | https://le.utah.gov/xcode/code.html and `C_1800010118000101.html` (the title index), gzip |

Checked for secret-like strings (none). Total about 340 KB.
