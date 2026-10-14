# Temporal and legal-validity model

Legal validity depends on time. ULIS never uses the latest text by default when the
question concerns an earlier date.

## Dates represented

| Date | Where | Example |
|---|---|---|
| Publication / enactment / adoption / introduction / signature | `instruments.work_date`, `lifecycle_events` | Act assented 2015-06-01; bill introduced 2026-04-15 |
| Commencement / entry into force | `provision_versions.valid_from`, `lifecycle_events(commencement / entry_into_force)` | 2015-07-01; treaty 2012-03-01 |
| Effective period | `[valid_from, valid_to)` per provision version | 2015-07-01 → 2022-01-01 |
| Amendment and repeal | New provision version (or `repealed` version) + `lifecycle_events` + `citation_links(amends/repeals/inserts)` | s 12(1) amended 2022-01-01; s 20 repealed 2023-03-01 |
| Prospective (enacted, not in force) | `status = prospective`, `valid_from = NULL` | s 12A |
| Proposed | `status = proposed`, `is_proposed` instrument | Bill 2026, clause 2 |
| Territorial scope | `extent[]` on instrument and provision | Provincial Act → `XA-NP` |
| Treaty status per party | `treaty_parties` (signature, consent, entry into force, withdrawal) | XA: in force 2012-03-01 |
| Currency (knowledge time) | `instrument_versions.currency_date` | Consolidation up to date to 2026-09-30 |
| Outstanding changes | `instrument_versions.outstanding_changes` | Digital Services Act 2026 not yet applied |
| Subsequent judgments | `citation_links` from judgment paragraphs, with decision dates | Minerva [2023] interprets s 12(1) |

## Semantics

- **Half-open intervals.** A version replaced on D has `valid_to = D`. The replacement has
  `valid_from = D`. On D itself the new version applies (tested at the boundary).
- **Derived vs explicit end dates.** If a source states an end date, it is `explicit`. If the
  end date is only implied by a later version's start, it is `derived` and labelled so in
  every response.
- **Two time axes.** *Valid time* (when the text was law) and *knowledge time* (what our
  sources told us, and when). A version with an open end is only known to be current up to
  its currency date. Asking about a later date yields `AS_OF_AFTER_CURRENCY`.
- **Containers.** A section's composite text changes whenever any in-force subsection
  changes. The parser therefore starts a new container version at the latest descendant
  change.
- **Transitional provisions** are ordinary provisions with their own periods. Their
  substantive effect (e.g. "until 30 June 2017") is text, not inferred validity. Phase 4 will
  add structured transitional rules where sources provide them.

## Point-in-time algorithm (`ulis/temporal/validity.py`)

```
resolve_at(versions_of_one_provision_in_one_language, D):
  dated := versions with valid_from and status in {in_force, repealed}, sorted by valid_from
  current := last v in dated with valid_from <= D < (valid_to or ∞)
  if current: status := current.status                        # in_force | repealed
  elif dated and D < first(dated).valid_from: not_yet_in_force
  elif any prospective: prospective_only
  else: no_version
  also return earlier, later and prospective versions for the timeline
```

## Case law and time

Judgments are not filtered out by date: a later decision may state what the law always
was. Whether that is so depends on the jurisdiction's rules on the retrospective effect of
judicial decisions. Hits decided after the requested date therefore carry
`DECIDED_AFTER_DATE`. Treatment of a *different* version of a provision (e.g. a 2020
decision on the "seven years" text shown alongside the "five years" text) carries
`TREATMENT_OF_OTHER_VERSION`.

## When dates cannot be verified

Unknown commencement (`EFFECTIVE_DATE_UNKNOWN`), missing currency (`CURRENCY_UNKNOWN`),
derived end dates, outstanding changes, and the absence of indexed treatment
(`CITATOR_COVERAGE`) are always disclosed. None of them is ever resolved by guessing.
