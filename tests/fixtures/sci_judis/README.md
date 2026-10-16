# Fixtures: Supreme Court of India judgments (JUDIS PDFs)

Eight real PDFs exactly as received (gzip-compressed, including the four-byte length prefix the server adds before `%PDF`),
fetched from `https://api.sci.gov.in/jonew/judis/<n>.pdf` on 2026-10-16 with the ULIS research User-Agent. Source descriptor:
`config/sources/in-sci-judis.yaml` (licence reading: AMBER, section 52(1)(q)(iv) Copyright Act 1957, to verify; the Court's website
terms could not be read).

| file | judgment | layout |
| --- | --- | --- |
| `judis_100.pdf.gz` | Nathmal Tolaram v. Superintendent of Taxes, Dhubri (18 Oct 1960) | JUDIS header, no numbered paragraphs, headnote |
| `judis_5000.pdf.gz` | Morcha v. State of Rajasthan (13 Sep 1978) | JUDIS header, criminal appeal, three reporter citations |
| `judis_15000.pdf.gz` | Smt. Gangadevi v. Union of India (1 Oct 1996) | JUDIS header, short order, bench on one line |
| `judis_30000.pdf.gz` | National Insurance Co. v. Deepa Devi (11 Dec 2007) | JUDIS header, numbered paragraphs |
| `judis_38000.pdf.gz` | Vimaleshwar Nagappa Shet v. Noor Ahmed Sheriff (11 May 2011) | signed judgment, paragraphs numbered "1)" |
| `judis_42000.pdf.gz` | State of Uttar Pradesh v. Preetam Singh (23 Sep 2014) | signed judgment followed by a Record of Proceedings |
| `judis_33827.pdf.gz` | State of U.P. v. Shiv Singh Yadav (16 Feb 2009) | signed order of a transfer petition, closing date "16TH FEBRUARY, 2009." |
| `judis_33824.pdf.gz` | (a Record of Proceedings only: Aastha Diagnostic Clinical Lab. v. State of J&K) | not a judgment: skipped by the parser |
