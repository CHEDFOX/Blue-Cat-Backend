# eCFR fixtures

Source: Electronic Code of Federal Regulations, XML files of the GPO bulk-data collection,
`https://www.govinfo.gov/bulkdata/ECFR/title-<N>/ECFR-title<N>.xml`, downloaded on 2026-10-14 with the
`ULIS-research-bot` User-Agent (robots.txt of govinfo.gov allows `/bulkdata`). Licence: U.S. Government work
(17 U.S.C. 105); the GPO's eCFR XML User Guide states "there are no restrictions on re-use". See
`config/sources/us-ecfr.yaml`.

| File | Content | Last-Modified (HTTP) |
|---|---|---|
| `ECFR-title1.xml.gz` | Title 1 whole, as downloaded (484,392 bytes before compression) | Tue, 11 Aug 2026 20:55:31 GMT |
| `ECFR-title12-excerpt.xml.gz` | Title 12: the header of the downloaded file and the whole of parts 21, 52, 220, 302 and 700, byte for byte | Tue, 06 Oct 2026 21:51:17 GMT |
| `ECFR-title17-excerpt.xml.gz` | Title 17: the header and the whole of part 450 | Fri, 09 Oct 2026 20:46:26 GMT |

In the excerpts only the wrapping `DIV1`/`DIV3`/`DIV4` start tags and headings that enclose each part were
re-assembled from the original (parts from different volumes of Title 12 sit under the first volume's
`AMDDATE`, "Oct. 1, 2026"); the parts themselves are untouched. Compressed only to keep the repository small.
