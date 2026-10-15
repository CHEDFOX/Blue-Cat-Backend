# Fixtures of the `oecd_legal_instruments` parser

Real bundles of the OECD Compendium of Legal Instruments (https://legalinstruments.oecd.org) read on 2026-10-15 with the ULIS
research bot, produced by the connector itself from its offline cache and gzip-compressed (the JSON bundle: the instrument's record
from `/api/instruments/<id>` and the HTML text from `bodyText.ref`, byte for byte). The record's `lastPublishedBy` (a staff member's
name and e-mail) is removed by the connector and is not in the fixtures. The licence is the one in `config/sources/int-oecd-legal-instruments.yaml`:
the notice on each instrument ("This document is provided free of charge. It may be reproduced and distributed free of charge
without requiring any further permissions, as long as it is not altered in any way. It may not be sold."); the Compendium itself
may not be reproduced "in whole or significant part", and these seven files are a small part of seven instruments.

| File | Instrument | What it exercises |
|---|---|---|
| `OECD-LEGAL-0162.en.json.gz`, `OECD-LEGAL-0162.fr.json.gz` | Recommendation on restrictive business practices relating to trademark licences | an abrogated instrument (abrogation 2023-06-08): `valid_to`, the repeal event; Roman paragraphs; the same labels in English and French |
| `OECD-LEGAL-0293.en.json.gz` | Convention on Combating Bribery of Foreign Public Officials | a convention: preamble and 17 articles, split by the text's own `Article N` headings |
| `OECD-LEGAL-0383.fr.json.gz` | Declaration on propriety, integrity and transparency | numbered paragraphs `1)`, `2)` ...; notes after the rule of underscores kept as a text block |
| `OECD-LEGAL-0307.fr.json.gz` | Decision on the Guidelines for Multinational Enterprises | Roman paragraphs with numbered paragraphs below them; a second numbering set (`Procédures`) named by its bold heading, accents folded in identifiers |
| `OECD-LEGAL-0449.en.json.gz` | Recommendation on Artificial Intelligence | `Section N:` headings, decimal principles `1.1.` ... `2.5.`, bold headings |
| `OECD-LEGAL-0413.en.json.gz` | Recommendation on Principles of Corporate Governance | an `APPENDIX` with lettered sub-principles `I.A.` ... (the largest fixture, 94 KB) |
