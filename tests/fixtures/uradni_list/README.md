# Uradni list RS fixtures

Three real act pages of Uradni list Republike Slovenije, the official gazette of Slovenia (https://www.uradni-list.si),
retrieved 2026-10-14 with the project's polite fetcher (identifying User-Agent, 5 s between requests), stored
gzip-compressed and byte for byte as received. They were fetched under the publisher's general terms
(https://www.uradni-list.si/podjetje/pravna-obvestila, 2.1: "Omejitve uporabe gradiv ne veljajo za Uradni list Republike
Slovenije in v njem objavljene vsebine") and its robots.txt (only `/_pdf/*/*a/` disallowed); see
`config/sources/si-uradni-list.yaml`.

| file | act | what it exercises |
|---|---|---|
| `ul-2025-01-3978-zjrm-2.html.gz` | Zakon o varstvu javnega reda in miru (ZJRM-2), Uradni list RS 112/2025 | current markup (`esegment_px`, `h4x`), the President's decree before the law, part headings, article headings, paragraphs, points, the closing signature lines |
| `ul-2025-01-3979-zpug.html.gz` | Zakon o potrjevanju učnih gradiv (ZPUG), 112/2025 | a short law with a numbered definition list |
| `ul-2006-01-2788-uredba-pis.html.gz` | Uredba o Pravno-informacijskem sistemu Republike Slovenije, 65/2006 | older markup (`esegment_p`, `h4`), a regulation without an abbreviation |

The tests rebuild the connector's cache from these files and read them offline.
