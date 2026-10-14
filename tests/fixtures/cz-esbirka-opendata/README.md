Fixtures for `tests/test_esbirka_opendata.py` - real data from the Czech e-Sbírka open-data dumps
(https://opendata.eselpoint.gov.cz/datove-sady-esbirka/, Ministerstvo vnitra ČR; files generated 2026-10-14, downloaded
2026-10-14 with the User-Agent `ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)`). Licence basis:
legislation is not protected by copyright (zákon 121/2000 Sb. § 3 písm. a); see `config/sources/cz-esbirka-opendata.yaml`.

- `dumps/001PravniAktZneni.json.gz`, `003PravniAktZneniFragment.json.gz`, `004PravniAktFragment.json.gz`,
  `006PravniAktMetadata.json.gz` - the records of the dumps of the same names that belong to two acts, 8/1918 Sb. (a one-
  page act of 1918 with an as-promulgated text and two consolidated texts) and 110/2019 Sb. (four versions), copied byte for
  byte and wrapped in the dumps' own layout (`{"položky":[ ... ]}`). They let the connector tests build a slice offline.
- `*.json.gz` - raw documents as the connector builds them from the full dumps (one version of one act): the Constitution
  1/1993 Sb. (versions from 1993-01-01, 2002-06-01 and 2026-01-01), 110/2019 Sb. (from 2019-04-24 and 2025-08-01) and the first
  version of the Labour Code 262/2006 Sb. (2006-06-07 to 2006-12-30, almost all of whose fragments take effect on 2007-01-01).

The files are gzip-compressed (the tests decompress them) to keep the repository small.
