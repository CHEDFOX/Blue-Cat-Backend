# Fixtures of the constitution parser

Real pages and real extracted text lines, retrieved 2026-10-15 with the connector (identifying User-Agent, robots.txt read first).
The publisher's terms are recorded in `config/sources/<id>.yaml` and `docs/constitutions-africa-asia-oceania.md`.

| File | What it is | Source |
|---|---|---|
| `cn-gov-cn.html.gz` | the page of the Constitution of the People's Republic of China (Chinese), gzip | www.gov.cn (`cn-govcn-constitution`) |
| `am-concourt-en.html.gz` | the English page of the Constitution of Armenia, gzip | concourt.am (`am-concourt-constitution`) |
| `za-en-pages.json` | the text lines (`pdftotext -layout`) of 9 pages of the English PDF of the South African Constitution | www.justice.gov.za (`za-doj-constitution`) |
| `lk-en-pages.json` | the text lines of 7 pages of the English PDF of the Sri Lankan Constitution | www.parliament.lk (`lk-parliament-constitution`) |
| `ke-en-pages.json` | the text lines of 5 pages (articles 76 to 86) of the Kenyan Constitution PDF | www.parliament.go.ke (`ke-parliament-constitution`) |

The JSON files hold one list of lines per page; `tests/constitution_pdf.py` writes them into a small PDF (built-in
Helvetica, cp1252 text only) so that the parser runs on a real PDF text layer without storing the publishers' PDFs.
Nothing in the fixtures was edited except that characters outside cp1252 were replaced by the writer's encoding fallback.
