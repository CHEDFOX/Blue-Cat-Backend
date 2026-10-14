# Normattiva fixtures (Italy)

Real exports from Normattiva ("esporta in Akoma ntoso", `https://www.normattiva.it/do/atto/caricaAKN`),
retrieved on 2026-10-14 with the ULIS research user agent as described in `config/sources/it-normattiva.yaml`.
The file names are those the site gives its downloads (`<GU date>_<redactional code>_ORIGINALE.xml` or
`..._VIGENZA_<date of the version>.xml`).

Licence: Normattiva's legal notice (https://www.normattiva.it/staticPage/legal) permits reproduction of the
electronic texts if the source, the non-authentic character and the free-of-charge character are stated. The
texts are not official; only the Gazzetta Ufficiale is authentic.

To keep them small, each file is the full export with the `<analysis>` block (modification index) removed and
only a few articles / attachments kept, byte for byte as received:

| File | Act | Kept |
|---|---|---|
| `19471227_047U0001_*` (6 files) | Costituzione, text as first published and the versions of 2001-11-08, 2012-05-08, 2021-11-04, 2022-03-09, 2023-10-22 | arts. 1, 2, 3, 9, 10, 41, 81, 115, 117, 125; transitional and final provisions I and XIII |
| `19420404_042U0262_VIGENZA_20260812.xml` | Codice civile (R.D. 262/1942) | the 2 articles of the approving decree; preleggi arts. 1, 12, 19; arts. 1, 2, 314/2, 1159 bis, 1519 novies, 2043, 2059 |
| `19301026_030U1398_VIGENZA_20260930.xml` | Codice penale (R.D. 1398/1930) | the 2 articles of the approving decree (of 3); arts. 1, 270 bis, 519, 575, 640 |
| `20030729_003G0218_VIGENZA_20260220.xml` | d.lgs. 196/2003 | arts. 1, 2-quindecies, 2-sexies, 4, 167; annexes B and C |
| `19700527_070U0300_VIGENZA_20251106.xml` | L. 300/1970 | arts. 1, 18, 33 |
| `19900818_090G0294_VIGENZA_20260421.xml` | L. 241/1990 | arts. 1, 2, 2-bis, 6, 22, 31 |

No secrets or credentials; the files contain only law text and Normattiva metadata.
