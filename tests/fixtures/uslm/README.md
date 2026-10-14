# USLM test fixtures (real US federal documents)

Unmodified copies of eight files from the US Government Publishing Office's USLM repository,
https://github.com/usgpo/uslm at commit `4ec9db2f4d5d8c18d54cbee5fd64cee9992e9311`:

| File | What it exercises |
|---|---|
| `115publ6-uslm.xml` | Public law as enacted (sections, paragraphs, findings) |
| `115publ11-uslm.xml` | Joint resolution with a single unnumbered section |
| `113publ287-uslm.xml` | Act enacting a whole US Code title "as follows" (quoted structure inside a section) |
| `COMPS-1656.xml` | Statute compilation (unofficial consolidated text, currency date) |
| `CFR-2018-title27-vol3-uslm.xml` | CFR edition volume with several parts, source notes, repeated paragraph numbers |
| `HJ37_RH.XML`, `HJ37_RFS.XML` | Two stages of one joint resolution (the later stage has only a lower-bound date) |
| `HR1000_IH.XML` | Simple resolution (not law) |

Licence: US Government works, not subject to copyright (17 U.S.C. 105), with a CC0 waiver;
see `LICENSE-usgpo-uslm.md`. The files are pinned by SHA-256 in `MANIFEST.yaml`.
