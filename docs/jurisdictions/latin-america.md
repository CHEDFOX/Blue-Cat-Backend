# Latin America: what was ingested and what was not

Checked on **2026-10-14** with ULIS's identifying agent (one request per page, no retry, no other identity, no proxy or
mirror). Only Uruguay could be ingested ([uy.md](uy.md)); Argentina was already there ([ar.md](ar.md)). The others are
recorded as `planned` sources (`config/sources/*.yaml` carry the quotes and URLs), the licence gate refuses them
(`tests/test_latam_planned.py`) and no profile or connector exists for them. (Brazil has since received a profile and its
STJ court decisions from an open data portal, which is a different source: [br-case-law.md](br-case-law.md); its legislation is still not loaded.)

| Country | Source | Result | Source YAML |
|---|---|---|---|
| Chile | Ley Chile (BCN) | Programmatic access needs an **API key** granted on request (form behind reCAPTCHA); the API conditions forbid mass, systematic or automated extraction to build an equivalent database without express authorisation, set 500 requests per key per 24 h, require authorisation for AI training, and state **CC BY-NC-ND 4.0** for the Library's contents (texts themselves declared outside copyright, "únicamente con fines informativos"). No robots.txt rules (SPA shell). | `cl-bcn-leychile` |
| Brazil | Planalto, LexML, Senado, Câmara | `www.planalto.gov.br` (robots.txt and a law page) closes the connection without a reply; `www.lexml.gov.br` resets the connection; `legis.senado.leg.br` answers 403 "Request forbidden by administrative rules". Câmara's robots.txt names AI crawlers but not ULIS; its pages were not pursued (consolidated text is Planalto's). | `br-planalto-legislacao` |
| Colombia | Secretaría del Senado | `www.secretariasenado.gov.co` resets the connection; Función Pública and SUIN-Juriscol present an incomplete certificate chain (TLS verification fails; not bypassed). | `co-secretaria-senado` |
| Peru | SPIJ | `spij.minjus.gob.pe` resets the connection; the Congress site's robots.txt disallows `/*.pdf$`. | `pe-spij` |
| Costa Rica | SCIJ / SINALEVI | Reachable, no robots rules, but "© Procuraduría ... Todos los derechos reservados" and no licence, open-data or bulk statement; interactive search application. | `cr-sinalevi` |
| Mexico | Cámara de Diputados "Leyes Federales Vigentes" | Reachable (the brief called it blocked; robots.txt is 404). Texts are **PDF/Word only**, the page calls the compilation "sólo de carácter informativo", no licence stated; ULIS has no PDF extractor. | `mx-diputados-leyes-federales` |

## Disclosure

Before the Ley Chile conditions had been read, one anonymous GET of the site's older XML service
(`https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma=172986`, the Código Civil, HTTP 200, 2.9 MB) was made to see what the
service returns. It was not kept in the repository or used, and nothing else was requested from it. Two redirects were also
followed from `https://www.impo.com.uy/bases` to IMPO's login page (under the disallowed `/cgi-bin/`) before the connector was
written; that page was not used and the connector never requests it.

## How to unblock

- **Chile**: ask the BCN for an API key and written authorisation for the set of norms (Constitución, Código Civil, Penal, del
  Trabajo, de Procedimiento Civil, Ley 19.628 ...). The legacy XML (`EsquemaIntercambioNorma`, a `fechaVersion` per structural
  unit) would give per-provision dates; the connector would be a `PoliteHttp` client sending the key from the environment.
- **Brazil, Colombia, Peru**: test from an ordinary network; if a host answers, read its terms and robots.txt first. Planalto's
  HTML needs a parser for struck-through repealed text and "(Redação dada pela Lei ...)" notes.
- **Costa Rica**: ask the Procuraduría for terms and a bulk or API route.
- **Mexico**: decide on a PDF text dependency, ask the Cámara (or counsel) for the terms, or use the Diario Oficial de la Federación.
