# Irish Statute Book fixtures

Real Acts of the Oireachtas as enacted, from the electronic Irish Statute Book (eISB, Office of the Attorney
General), XML rendition `https://www.irishstatutebook.ie/eli/<year>/act/<number>/enacted/en/xml`, retrieved on
2026-10-14 with `PoliteHttp` (retrieval times and final URLs in `meta.json`). Stored gzip-compressed, byte for byte.

| File | Act | Note |
|---|---|---|
| `1977-10.xml.gz` | Unfair Dismissals Act 1977 (No. 10) | full file |
| `1997-26.xml.gz` | Non-Fatal Offences against the Person Act 1997 (No. 26) | full file |
| `2001-50.xml.gz` | Criminal Justice (Theft and Fraud Offences) Act 2001 (No. 50) | full file |
| `2007-19.xml.gz` | Consumer Protection Act 2007 (No. 19) | full file |
| `2018-7.xml.gz` | Data Protection Act 2018 (No. 7) | full file (carries the publisher's bare `<!DOCTYPE act SYSTEM "legislation.dtd">`) |
| `1938-16.xml.gz` | Agricultural Produce (Cereals) Act 1938 (No. 16) | full file; opens with the processing instruction `<?version xml="1.0"?>` (retrieved 2026-10-15) |
| `2014-38-excerpt.xml.gz` | Companies Act 2014 (No. 38) | EXCERPT of the 4.4 MB file: metadata, front matter without the table of contents, sections 1-3, 228-229 with their Part/Chapter titles, and Schedule 5. The kept elements are byte-identical to the source. |

Licence: Oireachtas (Open Data) PSI Licence (incorporating CC BY 4.0), see `config/sources/ie-irish-statute-book.yaml`
(Houses of the Oireachtas copyright; not the official text of the Acts). The full Companies Act 2014 and the other
files are pinned by SHA-256 in `config/manifests/ie-irish-statute-book.yaml`.
