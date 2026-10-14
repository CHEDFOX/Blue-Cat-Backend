# ULIS fictional sample corpus

**Everything in this directory is fictional test data. None of it is real law.**

The Federal Republic of Aurelia (`XA`), its Northern and Southern Provinces (`XA-NP`,
`XA-SP`), the Kingdom of Borealis (`XB`) and the Northern Union (`XR`) are invented. The
texts were written for this repository to exercise the platform: point-in-time versions,
amendment and repeal, prospective provisions, territorial extent, bills, equally
authentic language versions, treaty ratification, binding and persuasive precedent, and
an overruled decision. Any resemblance to real legislation is generic legal drafting style.

Jurisdiction codes use ISO 3166 user-assigned ranges (`XA`-`XZ`), so they cannot collide
with real country codes.

## Format

Documents are Akoma Ntoso 3.0 XML (OASIS LegalDocML). ULIS-specific metadata uses the
`urn:ulis:akn-ext:1` namespace inside `<proprietary>` and as namespaced attributes
(`ulis:status`, `ulis:relation`, `ulis:extent`, `ulis:note`).

## Integrity

`MANIFEST.yaml` pins every file by SHA-256. The `local_manifest` connector refuses files
whose hash does not match. After editing a file, run `python scripts/update_manifest_hashes.py`.

## Licence

Dedicated to the public domain under CC0 1.0 (see `LICENSE`).
