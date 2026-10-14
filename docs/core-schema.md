# Jurisdiction-independent core schema

Implemented in `ulis/db/models.py` and `migrations/versions/0001_core_schema.py`.

## Modelling choices

- **FRBR layering (work → expression → source manifestation).** The abstract law
  (`instruments`, `provisions`, `judgments`) is separate from its language and time
  expressions (`instrument_versions`, `provision_versions`, `judgment_versions`). Those in
  turn are separate from the raw bytes they were read from (`source_documents`). The same
  model covers a consolidated statute, an equally authentic language version, a court's
  corrected judgment and a treaty text.
- **Stable identifiers.** Works and expressions use Akoma Ntoso-style URIs
  (`/akn/xa/act/2015/12`, `/akn/xa/act/2015/12/eng@2022-01-01`). Provisions use
  `<work>/~<eId>`, for example `/akn/xa/act/2015/12/~sec_12__subsec_1`. UUID primary keys
  are internal; URIs are the external contract and stay the same across re-ingestion.
- **Half-open validity** `[valid_from, valid_to)`. `NULL` means unknown or open, never
  "forever". `valid_to_basis` records whether an end date was published (`explicit`) or
  inferred from the next version (`derived`).
- **Jurisdiction-specific data goes in `ext` (JSONB), validated by the jurisdiction module.**
  Concepts whose legal meaning differs between systems (a "chapter number", a
  "*code_key*", an Indian "Act No.") are never added as core columns.
- **Originals, extractions and machine output are separate.** Translations and
  propositions live in their own tables, carry their producer, and link back to exact
  passages.

## Entities

| Requirement | Table(s) | Key fields |
|---|---|---|
| Jurisdiction and hierarchy | `jurisdictions` | `id` (e.g. `XA-NP`), `parent_id`, `kind`, `fictional`, `profile_version`, `profile_sha256`, `verification_status`, `corpus_status` |
| Legal system and authority rules | `jurisdiction_profile_versions` | Full profile JSON per version and SHA-256 (precedent doctrine, treaty reception, norm hierarchy) |
| Court and tribunal | `courts` | `jurisdiction_id`, `kind` (court/tribunal/administrative body), `rank`, `appeals_to[]`, `binds_self` |
| Source | `sources` | Descriptor JSON incl. coverage, access, licence; `licence_review_status` |
| Source version | `source_documents` | `sha256` (unique per source), `storage_key`, `retrieved_at`, `licence_id`, `uri`, `external_id` |
| Legal instrument | `instruments` | `uri`, `jurisdiction_id`, `instrument_type` (constitution, act, code, regulation, bill, treaty, ...), `is_proposed`, `extent[]`, `ext` |
| Instrument version | `instrument_versions` | `language`, `authoritative`, `text_origin`, `consolidation_kind` (as_enacted / consolidated / as_introduced), `version_date`, `currency_date`, `outstanding_changes` |
| Statutory provision | `provisions` | `eid`, `uri`, `kind`, `num_path[]`, `parent_id`, `extent[]`, `index_unit` |
| Provision version | `provision_versions` | Exact `text` + `text_sha256`, `status` (in_force/prospective/repealed/proposed), `valid_from`, `valid_to`, `valid_to_basis`, `note`, `source_locator` |
| Provenance and corroboration | `provision_version_sources` | Every expression and source document that attests a provision version |
| Judgment and judgment version | `judgments`, `judgment_versions` | `court_id`, `case_name`, `neutral_citation`, `decision_date`, `docket`; versions by language |
| Judgment paragraph | `judgment_paragraphs` | `eid`, `num`, `part` (background/motivation/decision), exact `text` + hash |
| Citation relationship | `citation_links` | `from_kind/from_id`, `to_uri` (as written), resolved `to_instrument_id/to_provision_id/to_judgment_id`, `relation` (cites, interprets, amends, repeals, inserts, implements, overrules, proposes_amendment, ...), `citation_text`, `extraction_method`, `verification_status` |
| Citation (formatted) | Computed by the jurisdiction module | Not stored as a fact. Formatting is a function of the module version. |
| Amendment, commencement, repeal, supersession | `lifecycle_events` | `event_type`, `event_date`, `affecting_uri/instrument_id`, `provision_id` |
| Treaty and treaty provision | `instruments` (type `treaty`) + `provisions`; `treaty_parties` | Signature, consent type/date, entry into force, withdrawal, reservations per party |
| Legal concept | `legal_concepts`, `concept_labels` | Multilingual retrieval labels. A label set is never an equivalence claim. |
| Legal issue | `legal_issues` | Key, jurisdiction, procedural stage (Phase 4) |
| Extracted fact, proposition, argument | `propositions`, `proposition_support` | `kind` (established_fact, allegation, disputed_fact, legal_provision, holding, inference, argument, contrary_interpretation, open_question), `produced_by`, `verification_status`; support rows link to an exact provision version or paragraph, with quote and `quote_verified` |
| Translation | `translations` | `kind` (official_translation / unofficial_human / machine), `provider`, kept apart from originals |
| Verification record | `verification_records` | `check` (source_integrity, parse, quote_exactness, version_text_consistency, ...), `result`, `details`, `checker` |
| Audit | `audit_events`, `ingestion_runs` | Who did what, when; run statistics and errors |
| Retrieval index (derived) | `passages` | Denormalised jurisdiction, dates, status, extent, language, `search_config` (regconfig), generated weighted `tsv` |

## Integrity constraints worth noting

- `provision_versions`: unique on `(provision, language, coalesce(valid_from,'-infinity'), status)`;
  `valid_to > valid_from`; enumerated `status` and `valid_to_basis`.
- `source_documents`: unique `(source_id, sha256)`, which makes ingestion idempotent.
- `passages` and `proposition_support` must point at exactly one target (check constraint).
- `propositions.kind` is enumerated, so a model-generated inference can never be stored as
  an "established fact" by omission.

## Lifecycle of a provision (example from the sample corpus)

| Provision | Version | Status | Valid | Basis | Attested by |
|---|---|---|---|---|---|
| `/akn/xa/act/2015/12/~sec_12__subsec_1` | "...seven years..." | in_force | 2015-07-01 → 2022-01-01 | derived | eng@2015-06-01 |
| same | "...five years..." | in_force | 2022-01-01 → (open) | open | eng@2022-01-01, eng@2023-03-01 |
| `/akn/xa/act/2015/12/~sec_12A` | "Backup copies" | prospective | (none) | open | eng@2022-01-01, eng@2023-03-01 |
| `/akn/xa/act/2015/12/~sec_20` | transitional text | in_force | 2015-07-01 → 2023-03-01 | derived | three expressions |
| same | (no text) | repealed | 2023-03-01 → | open | eng@2023-03-01 |

If two sources disagree about the text of the *same* version, the first recorded text stays
canonical. The conflicting text is not merged in. It is logged as a
`version_text_consistency` failure (with the offending source document) for human review.
Nothing is silently overwritten.
