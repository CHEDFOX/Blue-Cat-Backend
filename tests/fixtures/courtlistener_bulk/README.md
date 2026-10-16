# CourtListener bulk files: fixtures

Source: Free Law Project's public bulk files, generation 2026-09-30 (`com-courtlistener-storage.s3-us-west-2.amazonaws.com/bulk-data/`),
licence statement "free of known copyright restrictions" (Public Domain Mark 1.0), see `config/sources/us-courtlistener-bulk-s3.yaml`.

* `courts-2026-09-30.csv.gz` - the publisher's court table, unchanged (3,361 courts). The test that regenerates the US court list reads it.
* `bundles.json.gz` - 14 cases as `CourtListenerBulkConnector` yields them (JSON bundles of the court, docket and cluster rows, the citations
  and, per opinion, the verbatim value of the one text column used), taken from opinion rows read on 2026-10-16 with ranged requests at six
  places of the 55 GB opinions file. Because only those rows were read, a case that has several opinions in the publication can be present with
  only some of them (Fex v. Michigan: the lead opinion; the others are whole). The cases:
  Miller v. United States, 317 U.S. 601 (page units); Fex v. Michigan, 507 U.S. 43; Kiowa Tribe v. Manufacturing Technologies, 523 U.S. 751
  (lead opinion and dissent, pages of 118 S. Ct. 1700); Wallace v. OPM, 55 F. App'x 548 (Fed. Cir.); Altfelix v. Halliday, 79 Ohio St. (N.S.) 455;
  Water District No. 111 v. Moore, 65 Wash. 2d 392 (existing `US-WA-SC`); State ex rel. Moore v. Henderson, 323 So. 2d 474 (lead opinion and a
  concurrence); Wyatt v. Court of Common Pleas, 597 Pa. 56 / 950 A.2d 263 (Lawbox star pages); Benjamin v. Cone, 43 So. 2d 725; State v. Marks,
  2006 WI 108 and In re Arkansas Continuing Legal Education Board, 2017 Ark. 132 (neutral citations); Banks v. Clark, 1910 OK 513 (syllabus);
  The Trustees of the Estate of Bernice Pauahi Bishop v. Au (Haw. Ct. App. 2017, a docket number only); United States v. Ten Barrels & Three Kegs,
  28 F. Cas. 33 (N.D. Fla. 1869).
