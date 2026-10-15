# Basiswettenbestand (BWB) fixtures

Unmodified downloads from KOOP's open-data repository of the Basiswettenbestand
(https://repository.officiele-overheidspublicaties.nl/bwb/), retrieved on 2026-10-14 by `PoliteHttp`
with the ULIS User-Agent, stored gzip-compressed (`*.gz`) in the fetcher's cache layout
(`cache/repository.officiele-overheidspublicaties.nl/bwb/<BWB id>/...`, with the `.meta.json` it
writes, uncompressed). The tests decompress them into a temporary cache.

| Regulation | BWB id | Files |
|---|---|---|
| Uitvoeringswet Algemene verordening gegevensbescherming (UAVG) | BWBR0040940 | `manifest.xml` and all six toestanden (2018-05-25_0 ... 2026-09-01_0) |
| Grondwet | BWBR0001840 | `manifest.xml`, toestanden 2022-11-01_0 and 2023-02-22_0 (current) |
| Burgerlijk Wetboek Boek 6 | BWBR0005289 | `manifest.xml`, toestand 2026-07-16_0 (current) |
| Algemene wet bestuursrecht | BWBR0005537 | `manifest.xml`, toestand 2026-08-15_0 (current) |
| Besluit (Regeling vaststelling standaardpremie 2014) | BWBR0034337 | `manifest.xml`, toestand 2014-01-01_0 (a `<regeling>`; retrieved 2026-10-15) |
| Besluit of the Staatssecretaris (subsidieplafond) | BWBR0040488 | toestand 2018-02-15_0 only (a `<regeling>` with `regeling-tekst`; retrieved 2026-10-15) |

Licence: CC0 1.0, as stated for the "Basis Wetten Bestand" dataset and its downloads on
https://data.overheid.nl/dataset/basis-wetten-bestand (data owner KOOP); Acts are not protected by
copyright (art. 11 Auteurswet). The consolidated texts are not official: only the publications in
the Staatsblad and Staatscourant are.
