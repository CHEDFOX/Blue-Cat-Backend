# Jurisdiction-specific extension mechanism

Jurisdictions are added through **configuration plus a small, versioned code module**,
never by editing the core schema, retrieval or verification code.

## 1. Profiles (data): `config/jurisdictions/<id>.yaml`

A profile is the explicit, reviewable description of a jurisdiction. Schema:
`ulis/jurisdictions/profile.py` (strict; unknown keys are rejected).

| Section | Content | Used by |
|---|---|---|
| `id`, `parent`, `kind` | Position in the jurisdiction tree (international legal order → regional organisation / sovereign state → constituent unit → local) | Scope, extent, sovereign resolution |
| `profile_version`, `verification` | Semver; `fictional_test_data`, `draft_unverified` or `reviewed` (requires reviewer and date) | Limitations (`PROFILE_UNVERIFIED`, `FICTIONAL_DATA`) |
| `official_languages` | BCP 47 codes | Language preference, authentic-version notes |
| `legal_system` | families (common, civil, customary, religious, mixed, ...), structure, `treaty_reception` (monist / dualist / mixed) | Authority classifier |
| `legislative_institutions`, `instrument_types`, `norm_hierarchy` | Who legislates; which instrument types exist; their rank | Ingestion validation; authority |
| `courts` | id, name, kind, rank, `appeals_to`, `binds_self` | Precedent computation |
| `precedent` | doctrine (stare decisis / jurisprudence constante / none), `vertical_binding`, notes | Authority classifier |
| `citation.module` | Jurisdiction module id | Citation formatting, `ext` validation |
| `publication` | Official sources; commencement, amendment and repeal practice | Source planning; documentation |
| `memberships` | Regional organisations, with accession (and exit) dates | Search scope and authority: a member State's searches include the organisation's law and courts from the accession date; directives are marked as applying through national law |
| `coverage` | `corpus_status` and **known gaps** | Coverage reporting on every response |
| `extensions` | Module-specific settings, validated by the module | Module |

Rules that are enforced:

- Profile content is hashed (`sha256` of canonical JSON). Loading changed content under an
  *existing* `profile_version` fails (`ProfileVersionConflict`). Every change needs a version
  bump, and every loaded version is kept in `jurisdiction_profile_versions`.
- Fictional profiles must say so, and a real profile can never claim `fictional_test_data`.
- Parents must exist; cycles are rejected; `appeals_to` must reference known courts; norm
  hierarchy entries must be declared instrument types; official sources must exist in the
  source registry.

## 2. Modules (code): `ulis/jurisdictions/modules/`

```python
class JurisdictionModule:
    id = "default"; version = "1.0.0"
    ext_model = NoExtension                 # validates instrument/judgment `ext`
    profile_extension_model = NoExtension   # validates profile `extensions[<id>]`
    def pinpoint(self, c) -> str: ...
    def provision_citation(self, c) -> str: ...
    def judgment_citation(self, c) -> str: ...
```

Two contrasting modules ship today, both for fictional jurisdictions:

| Module | Legislation | Judgment |
|---|---|---|
| `xa` (common law) | `Personal Data Act 2015 (XA) s 12(1)` | `Minerva Holdings Ltd v Information Commissioner [2023] XASC 7 [4]` |
| `xb` (civil law, bilingual) | `art. 28 al. 2 CC-XB` / `Art. 28 Abs. 2 ZGB-XB` (by language) | `Cass. XB, 12 mars 2019, n° 2018-114, § 3` |
| `default` | `Northern Union Convention ... 2010, art 5(1)` | `Case name [neutral citation], para n` |

Module responsibilities stop at *presentation and validation*. Whether an authority binds is
decided by the generic classifier from profile data (`precedent`, `courts`,
`treaty_reception`). A module may refine that only for rules that cannot be expressed as data.
Each such refinement must have tests and a reviewer sign-off.

## 3. Why not a universal schema for everything?

Concepts that look alike are often legally different: a French *alinéa* is not an English
subsection; "binding" means different things under stare decisis and jurisprudence
constante; a treaty is "law" in a monist state but not in a dualist one. ULIS keeps:

- the **shared** layer minimal and structural (work, version, provision, interval, citation
  link, court graph);
- the **meaning** in profile rules and modules; and
- **cross-jurisdiction comparisons** explicit and labelled (concept labels are retrieval aids,
  never equivalence claims).

## 4. Onboarding checklist for a real jurisdiction (e.g. India, `IN`)

`config/jurisdictions/in.yaml` already exists as a **draft** (official languages, Supreme
Court, Article 141 note, dualist note). It is marked `draft_unverified` with
`corpus_status: none`. Search answers for `IN` therefore say *"No legal materials for IN are
indexed"* instead of returning nothing silently.

1. Counsel reviews the profile (courts incl. High Courts and tribunals, bench-strength
   rules, Article 141, treaty practice, commencement by notification, Gazette practice).
   Bump to `1.0.0` and set `verification.status: reviewed`.
2. Add constituent units (States and Union Territories) as child profiles where their
   legislation is in scope.
3. Write module `in` (citation formats such as Act No./year, AIR/SCC/neutral citations
   where licensed; `ext` schema for Act numbers and Gazette references).
4. Approve source licences (see [sources-and-licensing.md](sources-and-licensing.md)), then
   implement connector + parser with recorded fixtures.
5. Build the jurisdiction's evaluation set and pass the gates in
   [security-and-evaluation.md](security-and-evaluation.md) before exposing it to users.
