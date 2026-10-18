# Deploying ULIS on one VPS

One person, one server: PostgreSQL 16 + the API (one worker process: research jobs run in-process) + Caddy (automatic HTTPS), all from `deploy/`.
ULIS is a research aid for lawyers, never legal advice.

> **What was and was not tested.** Tested here: the new code (API hardening, `load-all`, `status`) against
> PostgreSQL 16, including a live uvicorn; the backup, restore, export and import scripts against a local
> PostgreSQL 16; `docker compose config` (the compose file parses, interpolates and fails fast without a password);
> the container's run-time layout emulated without Docker. **Not tested, because this sandbox has no Docker daemon, no
> systemd and no Caddy:** the image build, `docker compose up`, the Caddyfile (syntax checked by eye), the systemd
> units, TLS issuance, a full `load-all` against all publishers, and any load test. Expect small fixes on first deploy.

> **First release:** `docs/go-live.md` lists the nine countries loaded by default, the load times and the sizes.

## 1. Requirements

| | Minimum for the current corpus (about 740k provisions, more with all of Germany) | Notes |
|---|---|---|
| CPU / RAM | 4 vCPU / 8 GB | ingestion is CPU-bound for hours; PostgreSQL gets 2 GB `shared_buffers` (`.env`) |
| Disk | 100 GB SSD | measured in the build container with 741k provisions and 962k passages loaded: database 2.6 GB, raw documents 0.8 GB, download cache 1.1 GB; the full German corpus adds an estimated 2-3 GB more (extrapolated). The rest is for images, WAL and 3+ dumps |
| Bulk loads (e.g. all German federal laws, more countries) | 8 vCPU / 16 GB / 200 GB | raise `PG_SHARED_BUFFERS` to 25% of RAM; section 13 shrinks the footprint of the launch set from ~130 GB to ~30-35 GB without loss |
| OS | Ubuntu 24.04 or Debian 12, x86-64 | Docker Engine 24+ with the compose plugin |
| Network | public IPv4, ports 22/80/443 | outbound HTTPS to the publishers |
| DNS | an `A`/`AAAA` record for `ULIS_DOMAIN` | must resolve before the first start |

## 2. First deploy

```bash
sudo apt-get install -y docker.io docker-compose-v2 git ufw      # or Docker's own apt repository
sudo git clone https://github.com/CHEDFOX/Blue-Cat-Backend.git /opt/ulis && cd /opt/ulis/deploy
sudo mkdir -p /srv/ulis/objects /srv/ulis/sources /srv/ulis/backups
sudo chown -R 10001:10001 /srv/ulis/objects /srv/ulis/sources    # the containers run as uid 10001
sudo cp .env.example .env && sudo chmod 600 .env && sudo ${EDITOR:-nano} .env
#   fill ULIS_DOMAIN, ULIS_ACME_EMAIL, POSTGRES_PASSWORD (openssl rand -hex 24), ULIS_API_KEYS (openssl rand -hex 32)
sudo docker compose up -d --build        # first build takes a few minutes
sudo docker compose ps                   # db, api, caddy "healthy" after ~1-2 minutes
curl -s https://api.example.org/ready    # {"status":"ready", ...}
```

`.env` documents every variable. The API **refuses to start** with `ULIS_ENV=production` without API keys (24+
characters each), with the default or a short database password, or with `ULIS_CORS_ORIGINS=*`; the log names every
problem. The database and the API publish no ports; only Caddy listens on 80/443.

**DNS/TLS.** Caddy obtains and renews a Let's Encrypt certificate on its own once the domain resolves to the server
and ports 80/443 are reachable. Keep the `caddy_data` volume (certificates). `ULIS_DOMAIN=localhost` gives a trial
with a local, untrusted certificate.

## 3. First admin steps

1. Make one API key per client (`openssl rand -hex 32`): one for the web interface's server, one for yourself.
   Put them comma-separated in `ULIS_API_KEYS`, then `docker compose up -d api`.
2. Check the stack: `curl -H "X-API-Key: $KEY" https://$DOMAIN/v1/coverage` (200), and
   `docker compose --profile tools run --rm cli status` (empty until data is loaded).
3. Install the timers (section 6) and take a first backup: `./backup.sh`.
4. Do **not** load the fictional sample (`ulis-fictional-sample`) into a production database: it is invented law.
   `load-all` skips it unless you name it.

## 4. Loading the corpus

The corpus is not in git. `load-all` fetches it from the official publishers, politely (identifying User-Agent,
the publisher's crawl delay, back-off, refusals respected), and takes hours.

```bash
cd /opt/ulis/deploy
docker compose --profile tools run --rm cli load-all --dry-run     # the plan: order, sizes, minimum network time
docker compose --profile tools run -d --name ulis-load cli load-all --jobs 4  # detached; survives logout
docker logs -f ulis-load                                            # progress; `docker rm ulis-load` when done
docker compose --profile tools run --rm cli status                  # per-jurisdiction counts, last run, verification
```

* **What is loaded by default: the laws of the launch markets** (`config/launch-markets.yaml`: US, GB, EU, DE, FR, CA, AU,
  IN, with their states, provinces and regions). Three switches widen it: `--include-case-law` (sources that hold only
  court decisions), `--all-markets` (the countries outside the launch markets, mostly constitutions and statutes that earlier
  batches loaded) and `--include-history` (old editions such as the annual CFR and the Statutes at Large). `--only SRC`
  loads a named source whatever the defaults say. `load-all --dry-run` prints what each skipped group was skipped for.
* Every selected `status: active` source is loaded, **smallest first**; each source is its own transaction, so a failing source
  (publisher down, refusal, parse error) is reported and the run **continues**. Exit code 1 if any source errored,
  2 usage, 3 another `load-all` is running, 130 interrupted. `--only SRC...`, `--skip SRC...`, `--dry-run`.
* **Resumable**: downloads are cached under `/srv/ulis/sources` and ingestion is idempotent (documents are keyed by
  SHA-256). After a crash, reboot or Ctrl-C run the same command again; cached files are not fetched twice. Once the
  documents are verified the cache is a second copy: `--prune-cache` (or `cli prune-cache` later) deletes it (section 13).
* **Statistics during a load**: each source is one transaction, and the loader refreshes the planner statistics of the three big
  tables itself (`ANALYZE` inside the transaction, with a lock that keeps autovacuum off them until the source commits).
  So a manual `VACUUM`/`ANALYZE` on `provisions`, `provision_versions` or `provision_version_sources` waits until the running
  source commits; do the post-load `VACUUM ANALYZE` after `load-all` has finished.
* **Commercial use**: set `ULIS_COMMERCIAL_USE=1` in `.env` before any paid or public offering. The loader then refuses
  every source whose licence does not permit commercial use (today Washington, Texas, Oregon and Normattiva are
  "unknown"; the list is in [licence-review.md](licence-review.md)). The early-access test is not commercial.
* **Parallel**: `--jobs 4` loads four sources at once (default 1). Two sources whose publishers share a domain are never
  loaded together, so every publisher still sees one polite client; sources on different sites overlap. Sequentially
  the network time of the 14 sources the default plan loads is at least 158 hours (`--dry-run` prints it, at the delay
  each source will use; it was 229 hours before 2026-10-18). With `--jobs 4` the wall time is set by the three
  legislation.gov.uk sources, which share one host and so run one after another at robots.txt's Crawl-delay of 5 s (at
  least 10 + 25 + 91 = 126 hours, the secondary legislation as made being the largest), then Japan (at least 12.5 h) and
  the French dump, so plan for five to six days unless The National Archives grants a faster or bulk route
  ([permission-requests.md](permission-requests.md)). Since 2026-10-18 the request pace per source follows the
  publisher's own allowance ([onboarding-a-jurisdiction.md](onboarding-a-jurisdiction.md), section 3): where the
  publisher states no Crawl-delay or rate, 1 s between request starts on one connection (EU Cellar at least 9.4 h
  instead of 47 h in the dry run - about 19 h instead of 97 h counting every file - Finland 3.1 h instead of 15.5 h,
  Spain 1.9 h instead of 9.5 h, Germany 1.7 h instead of 8.5 h, Sweden 1.5 h instead of 7.4 h); where it does, its
  figure (UK 5 s, Australia 10 s, Japan 5 s, the last at Digital Agency's request not to loop over the API). Each
  source runs in its own transaction; use `--jobs 2` on a small VPS (memory: the French and Czech archives are
  streamed, the largest single documents are US Code titles and CFR parts).
* Time (estimates, not measured end to end): the smaller sources take minutes each (measured ingestion: Canada 2 min,
  UK from cache 3.5 min, US GPO samples 8 min; downloads add requests x crawl delay).
* **Germany**: the committed manifest (`config/manifests/de-gesetze-im-internet.yaml`) lists all 6,141 federal laws,
  so `load-all` alone fetches and loads them (a law the site no longer serves is reported as a run error and skipped; a
  refusal such as 403/429 stops the source and is respected). About 2.6% of the files hold no text (consumed
  amendment acts); they are counted as `documents_skipped`, not errors. `scripts/bulk_fetch_gii.py` (download only,
  resumable) and `scripts/build_gii_manifest.py [--all]` exist for running the download separately from a checkout with a
  virtualenv. The cache layout is identical (`var/sources/<host>/...`), so a cache downloaded on another machine can be
  rsynced into `/srv/ulis/sources` (then `chown -R 10001:10001`) and `load-all` will not download it again. A
  1,101-law trial from the cache ingested in 191 s. A source is one transaction, so if the German run is interrupted
  nothing from that run is stored, but every download is kept: run the same command again and it finishes from the
  cache in minutes instead of hours.
* **Skip the wait**: on a machine that already holds the data run `deploy/export-corpus.sh --with-objects`, copy the
  `ulis-corpus-*` files over and run `deploy/import-corpus.sh --objects ...-objects.tar.gz ...dump` on the new server.
  The export holds the corpus tables only (audit events, verification records and research results stay behind);
  the import replaces the target's database in one transaction, then `cli verify-sources` proves the transfer.

**Refresh.** `load-all --refresh` (weekly timer) re-runs only the *live* sources and discards cached downloads older
than 7 days first, so changed texts become new point-in-time versions; unchanged ones add nothing. *Static pins*
(manifest fixes a commit, release point, hashes or an explicit version list) are skipped because a re-run yields nothing
until you edit or regenerate their manifest: `us-gpo-uslm-samples`, `ca-justice-laws-xml`, `us-uscode-house`,
`ie-irish-statute-book`, `dk-retsinformation`, `ee-riigi-teataja`, `ch-fedlex`, `pl-eli-sejm`, `it-normattiva`,
`eu-eur-lex`. Live: UK, Germany, Netherlands, Spain, Japan, Finland, Sweden, Latvia, Taiwan, Argentina, Australia.
A weekly refresh of Germany re-downloads thousands of files: raise `--max-cache-age-days` or skip it (see the unit).

**New laws appear only after the manifest builders are re-run.** `load-all --refresh` never runs a builder: for each
selected source it discards the stale cached downloads and then calls the same `Ingestor.ingest_source` as `ingest`
(`ulis/ops/loading.py`), whose connector reads the committed `config/manifests/<source>.yaml`. So a refresh re-fetches only
the works already listed (a changed text becomes a new version), and a law enacted after the manifest was generated is
not fetched until the builder is re-run and the new manifest committed (the audit of 2026-10-18 found two norms missing
in Germany and 17 statutes with a suffixed SFS number in Sweden this way). Every builder serves the publisher's list from
the `PoliteHttp` cache when it holds one, so delete the cached list first (Germany `var/sources/www.gesetze-im-internet.de/
gii-toc.xml`, Spain `var/sources/www.boe.es/legislacion-consolidada/index-*.json`, Japan
`var/sources/laws.e-gov.go.jp/api2/laws/inforce-cur-offset*.xml`, Sweden `var/sources/data.riksdagen.se/dokumentlista@*`)
or run the builder after the weekly eviction. Builders for the launch markets: US `python scripts/build_uscode_manifest.py`
(`us-uscode-house`) and `python scripts/build_govinfo_manifests.py` (`us-govinfo-bulk-statutes`, `us-govinfo-bulk-cfr-fr`),
all three static pins; `us-ecfr` has no builder (its manifest is the fixed list of 49 CFR titles, whose files GPO regenerates,
so new regulations arrive with a refresh). GB `python scripts/build_legislation_gov_uk_manifest.py` (both legislation.gov.uk
manifests). FR has no builder: the LEGI connector applies every daily incremental archive the host lists after the pinned
stock (`incrementals.enabled: true`), so new texts of the selected natures arrive with a refresh, but a newer stock has to be
pinned by hand in `config/manifests/fr-dila-legi.yaml` (DILA's data sheet asks for the latest stock). DE
`python scripts/bulk_fetch_gii.py --limit 0` (fetches the table of contents only) then `python scripts/build_gii_manifest.py
--all`. EU `python -m scripts.build_eu_manifest` (static pin). CA `python scripts/build_justice_canada_manifest.py
/path/to/laws-lois-xml --all` (static pin; needs a checkout of github.com/justicecanada/laws-lois-xml). AU
`python scripts/build_frl_manifest.py`. ES `python -m scripts.build_boe_manifest`. SE `python scripts/build_sfs_manifest.py`.
FI `python -m scripts.build_finlex_manifest`. JP `python -m scripts.build_egov_manifest` (the texts come from e-Gov's bulk
archive, which `--refresh` downloads again once the cached one is older than the refresh age, `docs/jurisdictions/jp.md`). A static pin stays skipped by
`--refresh` after its manifest changed: load it with `load-all` without `--refresh`, or `--only`. Running the builders on a
schedule is not implemented.

## 5. Upgrading

```bash
cd /opt/ulis/deploy && ./backup.sh && sudo git pull
sudo docker compose build api && sudo docker compose up -d     # migrations and catalog sync run on start
curl -s https://$DOMAIN/ready                                  # must say "ready"
```
Downtime is the API restart (about a minute). Migrations are forward-only: to roll back, `git checkout` the old
version, rebuild, and `./restore.sh` the backup taken before. Tag images with `ULIS_VERSION` to keep the previous one.
Update the base images now and then: `docker compose pull db caddy && docker compose up -d`.

## 6. Backups, restore, timers

* `./backup.sh` writes `ulis-<utc>.dump` (pg_dump, zstd-compressed where pg_dump supports it, else gzip; checksummed;
  read back in full to verify) into
  `ULIS_BACKUP_DIR` (default `deploy/backups`), keeps 14 days (`ULIS_BACKUP_KEEP_DAYS`) and never fewer than 3.
  The dump contains audit events and research results: store it like the database. Copy it **off the server**
  (`rsync`, `rclone`, a storage box); a backup on the same disk is not a backup.
* The raw documents (`/srv/ulis/objects`) are immutable and content-addressed: `rsync -a --ignore-existing` them
  off-site. Earlier consolidations that publishers no longer serve exist only there.
* `./restore.sh [--yes] DUMP` replaces the database (one transaction, API stopped meanwhile). Try a restore once
  on a scratch machine before you need it.
* `deploy/systemd/`: `ulis-backup.timer` (nightly 02:30), `ulis-refresh.timer` (Sunday 03:30, `load-all --refresh`),
  `ulis-verify.timer` (monthly `verify-sources`). Copy to `/etc/systemd/system/`, fix the path, then
  `systemctl enable --now ulis-backup.timer ulis-refresh.timer ulis-verify.timer`. A failed run shows in
  `systemctl --failed`; add an `ExecStartPost=curl -fsS <healthchecks URL>` to be told when one stops running.

## 7. Monitoring

* `GET /health` liveness (no database), `GET /ready` readiness: database reachable, migrations at head, catalog loaded;
  503 with the failing check otherwise. Point an external uptime monitor at `/ready`.
* Logs: `docker compose logs -f api` is one JSON object per request (`request_id`, `method`, `path`, `status`,
  `duration_ms`, `client_ip`, `principal` = key fingerprint, `limited_by`); never query strings, bodies or keys.
  Examples: `... | grep '"status":5'`, `... | grep limited_by`. Caddy's log is JSON too, with the query cut off.
* `cli status` (add `--json`), `df -h /srv/ulis`, `docker system df`, `systemctl list-timers 'ulis-*'`.

## 8. Security checklist

- [ ] Firewall: `ufw default deny incoming; ufw allow 22,80,443/tcp; ufw allow 443/udp; ufw enable`. Docker writes its own
      iptables rules and bypasses ufw for *published* ports: that is why only Caddy publishes any. Never add `ports:`
      to `db` or `api`.
- [ ] SSH: keys only (`PasswordAuthentication no`, `PermitRootLogin no`), a non-root sudo user, `unattended-upgrades`.
- [ ] `deploy/.env` is `chmod 600`, never committed (git-ignored), not pasted into tickets. Dumps are `0600`.
- [ ] One API key per client. **Rotate**: append the new key to `ULIS_API_KEYS`, `docker compose up -d api`, switch the
      client, remove the old key, `up -d api` again. Rotate when a laptop or person leaves.
- [ ] The web interface's *server* calls the API with its key; the key never goes to a browser, so leave
      `ULIS_CORS_ORIGINS` empty. The API sees only the web server's address, so the web app must rate-limit its own users.
- [ ] Rate limits are per process (one worker). Defaults: 600/min per key, 300/min per IP, research
      60/h per key and IP, 300/day for everybody. Tighten with the `ULIS_RATE_LIMIT_*` variables.
- [ ] Off-site, ideally encrypted, backups; a tested restore. Keep Docker, the images and the OS patched.
- [ ] Before anyone pastes a client's case file: pasted text goes to the LLM provider. Agree the provider's data
      terms (retention, training) and tell testers. The research API is specified not to store or log case text (stored results, which may
      summarise the case, are deleted after 24 h); check that on the version you deploy.

## 9. LLM cost

Retrieval (`/v1/search` and friends) costs nothing per call. Each research job calls the model several times (understand,
search steps, draft, verify), re-sending the case text and retrieved authorities, so a long case file is many thousands of
input tokens per call. No prices are stated here: they change. Read `usage.input_tokens/output_tokens` in each result, multiply
by the provider's current price list, and compare with the `ANTHROPIC_API_KEY` spend in the provider console. Set a hard
monthly spend limit there. The worst case per day is `ULIS_RATE_LIMIT_RESEARCH_GLOBAL` jobs x cost per job: start low
(for example `50/day`) and raise it with evidence. `depth: deep` uses a stronger model and costs more.

## 10. Not production-ready (be honest with testers)

* **No multi-tenant case-file workspace** (designed in `architecture.md` section 5, not implemented): nothing persists a lawyer's
  matter; research results live 24 h, and one shared pool of API keys serves everybody.
* **No user accounts**: API keys identify *clients*, not people. Login, per-user history and quotas belong in the web layer.
* **Single-node database**, one server: no replication or failover; the recovery point is the last dump, the recovery time
  is a restore. Rate limits are in memory and per worker, not shared.
* Profiles and parsers are not independently reviewed (see `roadmap.md`); every answer needs a lawyer's check.
* No load test has been run; the single-worker, 8 GB sizing is a reasoned starting point, not a measured capacity.

## 11. Troubleshooting

| Symptom | Look at |
|---|---|
| Caddy has no certificate | DNS resolves to this server? ports 80/443 open? `docker compose logs caddy`; Let's Encrypt rate limits (use `localhost` to test) |
| `api` restarts | `docker compose logs api`: `refusing to start ...` lists every unsafe setting; DB password with `@ / :` breaks the URL (use hex) |
| 502 from Caddy | api still migrating (first start) or unhealthy: `docker compose ps`, `logs api` |
| `/ready` 503 | the `checks` object: migrations behind -> `docker compose up -d api`; catalog -> `cli sync-catalog`; database -> `logs db` |
| 401 | key not in `ULIS_API_KEYS`, stray space, or `.env` edited without `docker compose up -d api` |
| 429 | `Retry-After` header; `limited_by` in the log says which limit (`ip`, `key`, `research_*`) |
| 413 | body above `ULIS_MAX_BODY_BYTES` (`..._RESEARCH` for `/v1/research*`) or Caddy's 2 MB |
| `load-all` exit 3 | another run holds the lock (`docker ps`); it frees itself when that run ends |
| a source "failed" | publisher refused or was down: read the message, wait, rerun `--only SRC`; never loop against a refusal |
| `permission denied` on `/data` | `sudo chown -R 10001:10001 /srv/ulis/objects /srv/ulis/sources` |
| slow search after a bulk load | `docker compose exec db psql -U ulis -c 'VACUUM ANALYZE'` |
| disk full | `cli prune-cache` (cached downloads of verified sources; raw copies are in `objects`), `cli compact-objects` (objects stored before compression), fewer dumps on the server, `docker system prune`; section 13 |

## 12. Files

`deploy/Dockerfile` (multi-stage, non-root, healthcheck), `docker-compose.yml`, `Caddyfile`, `.env.example`,
`backup.sh`, `restore.sh`, `export-corpus.sh`, `import-corpus.sh`, `lib.sh`, `systemd/`; code in `ulis/api/security.py`
and `ulis/ops/`. The root `docker-compose.yml` is a development database only (`docker compose up db`); there is no second API image.
Tested on: Python 3.13, PostgreSQL 16.15 clients/server, Starlette/uvicorn as pinned in the venv.

## 13. Least disk space, no loss

Everything below is **lossless**: every stored text and every raw byte comes back exactly, `verify-sources` still
re-derives every text from the original bytes, and search results do not change. Nothing is thrown away except copies
of bytes that are kept elsewhere (the download cache once its documents are verified).

### Expected footprint of the launch set

| What | Without | With | How |
|---|---|---|---|
| PostgreSQL data directory | ~60 GB | **~23-27 GB** | ZFS or btrfs with zstd under `/srv/ulis/postgres` (below): x2.24 (ZFS `recordsize=32K`) to x2.66 (btrfs, or ZFS `recordsize=128K`) simulated on the eCFR database's files; x2.99 compressing whole files is the upper bound no filesystem reaches |
| Raw source documents (`/srv/ulis/objects`) | ~35 GB | **~6-7.5 GB** | stored zstd-compressed by the application (`ULIS_OBJECT_COMPRESSION_LEVEL`, default 9): x5.70 measured on the whole eCFR store; documents that are already zip archives (Germany's `xml.zip`, the US Code zips, the French LEGI bundles) stay as they are (x1.0) |
| Download cache (`/srv/ulis/sources`) | ~35 GB | **0** after `prune-cache` | only `robots.txt` files stay; it fills again during a load (about x4-5 smaller on a zstd filesystem) |
| Each database dump (`backup.sh`) | ~8.0 GB (gzip-6) | **~6.3 GB** | pg_dump `--compress=zstd:9` instead of gzip level 6 (eCFR database: 851 -> 669 MB, below) |

Steady state: about 30-35 GB of data plus the dumps you keep on the server, against ~130 GB before. The peak is during
the first load, before `prune-cache` runs: the cache holds ~35 GB on top (~8 GB when `/srv/ulis/sources` is on the
compressed filesystem too, as in the ZFS recipe below) and PostgreSQL's WAL up to `max_wal_size` (4 GB). With three
dumps (~19 GB) and the Docker images (~5 GB) that peak is ~100 GB with an uncompressed cache and ~70 GB with a compressed
one: the launch set fits a **200 GB disk with 100 GB or more to spare**, and a 100 GB disk if the dumps are kept off the
server. With the default retention (`ULIS_BACKUP_KEEP_DAYS=14`) fourteen nightly dumps alone take ~14 x 6.3 GB: keep only
the newest 3 on the server (`ULIS_BACKUP_KEEP_DAYS=0`) and the history off-site.

### Raw source documents: zstd in the object store

`LocalObjectStore` writes each raw document as `<root>/sha256/ab/cd/<digest>.zst`, one zstd frame with its own checksum.
`<digest>` is the SHA-256 of the **original** bytes, exactly as before: keys, `source_documents.sha256` and every
provenance link are unchanged. Reading decompresses and checks the SHA-256 of the decompressed bytes (a damaged or
substituted file is an integrity error, as before). Objects written by earlier versions (plain `<digest>` files) are
read as they are.

Level measurements (python zstandard 0.25, each object compressed on its own as the store does, CPU time of one core):

| Level | Whole eCFR store (8,332 objects, 974.5 MB) | 1,000-object sample (133.4 MB) | Other publishers' fixtures (642 files, 69.5 MB) | Compression speed |
|---|---|---|---|---|
| 3 | 197.2 MB, x4.94 | x5.08 | x5.09 | 218 MB/s |
| 6 | | x5.69 | x5.49 | 85 MB/s |
| **9 (default)** | **170.9 MB, x5.70** | x5.95 | x5.59 | **50 MB/s** |
| 12 | 167.6 MB, x5.81 | x6.09 | x5.65 | 19 MB/s |
| 15 | | x6.27 | x5.73 | 5.2 MB/s |
| 19 | 156.5 MB, x6.23 | x6.61 | x5.86 | 1.9 MB/s |
| 19 + long-distance matching (window 128 MB) | | x6.64 | | 1.9 MB/s |

Decompression runs at 460-1,400 MB/s at every level, so `verify-sources` and the API do not notice it. Per publisher
(fixtures, level 9 / 19): UK legislation.gov.uk XML x6.5 / x6.8, Spain BOE x10.9 / x12.1, Japan e-Gov x11.1 / x12.6,
EU Cellar x7.9 / x8.4, Sweden x6.2 / x7.1; zip and PDF documents x1.0-1.5 at any level. A zstd dictionary trained on
3,000 other eCFR objects (400 s to train) saved 0.8 points more at level 3 (x5.2 instead of x5.0 on 2,000 objects) and
is not used; xz -9e (x6.7, 0.8 MB/s) is no better than zstd-19.

Why level 9: it keeps 98% of the bytes level 19 saves (the output is 9% larger) for 1/26 of the CPU. Ingestion handles
about 0.3 MB/s of raw documents per process (the eCFR: 826 MB in about 51 minutes), so level 9 adds ~0.5% to a load;
level 19 would add ~14% and a 35 GB store would take ~5 CPU-hours to compress. Long-distance matching does not help
documents of this size.

* `ULIS_OBJECT_COMPRESSION_LEVEL` (`.env`): 1-22, default 9; `0` stores new objects uncompressed.
* Measured end to end on a copy of the eCFR store: `compact-objects --jobs 4` converted the 8,332 objects in 54 s
  (974,535,338 -> 170,891,758 bytes; 978 MiB -> 211 MiB on disk with 4 KB blocks), a second run changed nothing (9 s), and
  `verify-sources` re-derived 251,842 provision texts of 305 documents (the five largest and 300 at random) from the
  `.zst` objects: all passed.
* `cli compact-objects [--level N] [--recompress] [--jobs N]` converts the uncompressed objects in place and prints the
  bytes before and after. Each object is replaced only after its compressed file was written, synced, read back and its
  SHA-256 checked; an interrupted run leaves every object readable and the same command resumes (it is idempotent). A
  file whose bytes do not match its key is reported and left alone. `--recompress --level 19 --jobs 4` is an optional
  offline pass that rewrites existing `.zst` objects when they get smaller (8% on the eCFR store: 170.9 -> 156.5 MB;
  ~5 CPU-hours per 35 GB of raw documents, ~1.3 hours with four cores).
* Backups, export and import handle both kinds of object: `backup.sh --with-objects` and `export-corpus.sh --with-objects`
  archive the directory as it is (`.tar.gz`, as before: the `.zst` files do not shrink again, but gzip removes the tar
  headers and padding, 7% of a plain `.tar` of 2,000 eCFR objects), and `restore.sh` / `import-corpus.sh` unpack it as it
  is. An off-site `rsync -a --ignore-existing` copy keeps working; after `compact-objects`, one `rsync -a --delete`
  drops the uncompressed copies there.
* A server running an older ULIS version cannot read `.zst` objects: upgrade before importing an object archive made by
  this version.

### Database dumps: zstd

`backup.sh` and `export-corpus.sh` ask pg_dump for `--compress=zstd:9` when it supports zstd (PostgreSQL 16 and later
built with libzstd, as the `postgres:16` image and Ubuntu 24.04's client are; checked before each dump with a probe that
dumps nothing) and fall back to gzip level 6 otherwise. `ULIS_BACKUP_ZSTD_LEVEL` changes the level, `ULIS_BACKUP_COMPRESS` sets
any `--compress` value. The dump is then read back in full (every data block decompressed, `pg_restore --file=-` to
nowhere) before it is kept; `restore.sh` and `import-corpus.sh` take either kind (pg_restore reads the method from the
archive; a zstd dump needs pg_restore 16+).

Measured on the eCFR database (6,379 MB, `pg_database_size` 6,084 MiB; 4.54 GB of SQL when restored to text), pg_dump
16.15, custom format, in a shared 4-vCPU build container whose load average was 3-19 (12-19 for all but the zstd:19 run),
so the times are upper bounds:

| `--compress` | Dump size | Ratio | Dump time | Full read-back | Restore (one transaction, indexes rebuilt) |
|---|---|---|---|---|---|
| `6` (gzip, the old setting) | 851,423,032 B | x7.5 | 129 s; 195 s at the load of the zstd:9 run | 28.5 s | 442 s |
| `zstd:3` | 741,534,119 B | x8.6 | 47 s | - | 538 s |
| **`zstd:9` (default)** | 669,015,692 B | **x9.5** | 150 s | 8.1 s | - |
| `zstd:19` | 578,820,497 B | x11.0 | 4,676 s (78 min) | - | - |

Restore time is index building and loading, not decompression (the read-back column is the decompression), so it does
not depend on the method; the two restores differ by the container's load. zstd:9 is 21% smaller than the old gzip
dumps, faster to write at the same load (150 s against 195 s) and 3.5 times faster to read back. zstd:19 saves another
13.5% but took 31 times as long (about 13 hours for a 60 GB database): not for a nightly dump; for a one-off archive,
`ULIS_BACKUP_COMPRESS=zstd:19 ./export-corpus.sh`.

### Download cache: `prune-cache`

Connectors cache every download under `/srv/ulis/sources` (`ULIS_SOURCE_CACHE_DIR`) so that a load resumes without
fetching again. Once a source's documents are in the object store and verified, the cache is a second copy.

```bash
docker compose --profile tools run --rm cli prune-cache --dry-run    # what would go, per source and directory
docker compose --profile tools run --rm cli prune-cache              # delete it
docker compose --profile tools run -d --name ulis-load cli load-all --jobs 4 --prune-cache   # prune at the end
```

How it decides (`ulis/ops/cache.py`):

* A source is prunable only when it has stored documents, **every** one of them has a `verify-sources` result (a
  re-derivation from the stored bytes, not the hash check ingestion records when it stores a document) and the latest
  one passed, and its latest ingestion run `completed` (not `failed`, not `completed_with_errors`).
* The cache is organised by host, not by source. A host directory (both spellings, with and without `www.`, the same
  host list `load-all --refresh` evicts by) is deleted only if every source naming that host that has been loaded at all
  (stored documents or an ingestion run) is prunable; one failed, erroring or unverified source keeps the directory
  whole. Sources never loaded do not hold a directory back. Git snapshots (`<owner>__<repo>`, Canada) are deleted the same
  way. Directories of hosts no manifest names are never touched, symbolic links are never followed, and `robots.txt`
  (with its `.meta.json`) stays: `load-all --dry-run` reads the Crawl-delay from it.
* `prune-cache` holds the `load-all` lock, so it never runs during a load. `--only SRC...` limits it to some sources.
* Planned read-only against the loaded eCFR database and its cache: `us-ecfr` has 8,124 of 8,124 documents verified,
  so `www.govinfo.gov` goes (98 files, 785.9 MB, `robots.txt` kept); the other govinfo sources were never loaded and do
  not hold it back.
* `load-all --prune-cache` runs only when no source of the run failed (a failed source resumes from its cache). It first
  runs the `verify-sources` check on the documents of the sources it loaded that have none yet, then prunes those
  sources. A document that fails keeps its source's downloads, and the run exits 1.

The trade-off: a later run of a pruned source downloads what it needs again, at the publisher's crawl delay.

* `load-all --refresh` (the weekly timer) selects the live sources only and first deletes their cached downloads older
  than `--max-cache-age-days` (7). With an empty cache there is nothing to delete, and every connector fetches each file
  of its manifest again: `PoliteHttp` serves from the cache only, it sends no conditional requests (`If-Modified-Since`,
  `ETag`), so a missing file is a full download. A weekly refresh does nearly that anyway (it deletes every cached file
  downloaded more than seven days earlier before the source runs), so pruning costs it almost no extra network time (it does with a larger `--max-cache-age-days`:
  the files a longer age would have kept are fetched again). Ingestion then stores only what changed (documents
  are keyed by SHA-256; unchanged ones count as "seen"). Static pins are skipped by `--refresh`, so their pruned downloads
  are fetched again only when they are loaded again (`load-all` without `--refresh`, or `--only`).
* A plain `load-all` re-runs every selected source, and every pruned one is downloaded again in full (connectors do not
  consult the database), taking the network time of a first load: re-run a single source with `--only SRC`.
* Bulk archives are downloaded again whole when their source runs again: the French LEGI stock and its daily
  increments, the Finnish and Japanese bulk packages where those connectors use them, the US Code release zips; a git
  snapshot is cloned again.
* Manifest builders that serve the publisher's list from the cache (section 4) fetch a fresh list after a prune.

### PostgreSQL data directory on a compressing filesystem

PostgreSQL has no table-level compression beyond TOAST, which only compresses values over about 2 KB (`pglz`); the bulk
of the corpus is many small rows, indexes and search vectors, which TOAST does not touch. A filesystem that compresses every
block with zstd shrinks all of it. It is lossless and transparent: PostgreSQL reads and writes 8 KB pages through the
ordinary system calls, the filesystem returns exactly the bytes written, `fsync` keeps its meaning, and both ZFS and btrfs
checksum every block on top of zstd's own lossless format. Nothing in PostgreSQL or ULIS changes.

What it gives, simulated on the files of the eCFR database (6,379 MB, 8,124 documents; read-only): each record or chunk
compressed on its own with zstd-3 and rounded up to 4 KB sectors, as the filesystems store them (ZFS with `ashift=12`
keeps a record uncompressed unless it saves 1/8; all-zero records are holes). No ZFS or btrfs was mounted in the build
container, so take these as estimates within a few percent:

| Layout | Stored | Ratio | 60 GB database |
|---|---|---|---|
| zstd-3 over whole files (upper bound) | 2,134 MB | x2.99 | 20 GB |
| ZFS `recordsize=8K` | 4,038 MB | x1.58 | 38 GB |
| ZFS `recordsize=16K` | 3,327 MB | x1.92 | 31 GB |
| **ZFS `recordsize=32K`** | 2,851 MB | **x2.24** | 27 GB |
| ZFS `recordsize=64K` | 2,566 MB | x2.49 | 24 GB |
| ZFS `recordsize=128K` | 2,403 MB | x2.65 | 23 GB |
| btrfs `compress-force=zstd:3` (128 KB chunks) | 2,402 MB | x2.66 | 23 GB |

**ZFS (recommended).** Ubuntu 24.04 ships OpenZFS 2.2 (`zfsutils-linux`).

```bash
sudo apt-get install -y zfsutils-linux
# a second disk or a free partition, e.g. /dev/disk/by-id/... or /dev/vda3
sudo zpool create -o ashift=12 -O compression=zstd -O atime=off -O xattr=sa -O mountpoint=none ulis /dev/vda3
sudo zfs create -o mountpoint=/srv/ulis ulis/data                     # objects, sources, backups inherit zstd
sudo zfs create -o recordsize=32K -o mountpoint=/srv/ulis/postgres ulis/data/postgres
sudo mkdir -p /srv/ulis/objects /srv/ulis/sources /srv/ulis/backups
sudo chown -R 10001:10001 /srv/ulis/objects /srv/ulis/sources
# Point the compose volume at it BEFORE the first `docker compose up` (the volume name is ulis_pgdata):
sudo docker volume create --driver local --opt type=none --opt o=bind --opt device=/srv/ulis/postgres ulis_pgdata
# Cap the ARC so it does not compete with shared_buffers (here 4 GB on a 16 GB server):
echo "options zfs zfs_arc_max=4294967296" | sudo tee /etc/modprobe.d/zfs.conf && sudo update-initramfs -u
```

`compression=zstd` is zstd level 3: fast enough for PostgreSQL's writes, and ZFS stores a block uncompressed when it
would not save 1/8, so the `.zst` objects and the dumps cost no space. `recordsize`: ZFS compresses each record on its
own and rounds the result up to 4 KB sectors, so small records lose most of the gain (8K, PostgreSQL's page size: x1.58;
16K: x1.92); 32K keeps three quarters of the file-level ratio (x2.24) while a random 8 KB page read reads and decompresses
at most 32 KB and a page write rewrites at most 32 KB, which is the usual balance for PostgreSQL on compressed ZFS.
For the smallest database use `recordsize=128K` (x2.65, ~23 GB instead of ~27 GB): this corpus is loaded in bulk and
then mostly read, which suits large records, but random page writes during a load become 128 KB read-modify-writes.
`atime=off` avoids a metadata write on every read. Docker warns that the volume was not created by compose; that is
expected.

**btrfs.**

```bash
sudo apt-get install -y btrfs-progs btrfs-compsize
sudo mkfs.btrfs -L ulis /dev/vda3
sudo mkdir -p /srv/ulis
echo 'LABEL=ulis /srv/ulis btrfs compress-force=zstd:3,noatime 0 0' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount /srv/ulis
sudo btrfs subvolume create /srv/ulis/postgres
# then the same mkdir/chown and `docker volume create ... device=/srv/ulis/postgres ulis_pgdata` as for ZFS
```

`compress-force` compresses every extent (plain `compress` gives up on a file after an incompressible start);
btrfs compresses up to 128 KB at a time (x2.66 above). Pages PostgreSQL rewrites later land in new, smaller extents
(copy-on-write), which compress less well, so the ratio of a database that is updated a lot drifts down; a reload or
`btrfs filesystem defragment -r -czstd /srv/ulis/postgres` (with the database stopped) restores it. Do **not**
`chattr +C` (nodatacow) the PostgreSQL directory: it switches off both compression and checksums.

**Moving an existing database** (the API stopped for the duration): `./backup.sh`, then
`docker compose stop api db`, `sudo rsync -aHAX /var/lib/docker/volumes/ulis_pgdata/_data/ /srv/ulis/postgres/`,
`docker compose rm -f db && docker volume rm ulis_pgdata`, the `docker volume create` line above, `docker compose up -d`.
Or create the new volume empty and `./restore.sh` the dump (which also rebuilds every index compactly).

**A single-disk VPS.** Best: before loading anything, reinstall with a separate partition, if the provider's installer
allows custom partitioning (Hetzner `installimage`, OVH, netcup and others do): 40 GB for `/` (ext4) and the rest
unformatted for the pool. Otherwise, a pool on a file image works, at a cost:

```bash
sudo fallocate -l 150G /var/lib/ulis-zfs.img        # preallocated, never sparse: a full host disk would fail the pool
sudo zpool create -o ashift=12 -O compression=zstd -O atime=off -O xattr=sa -O mountpoint=none ulis /var/lib/ulis-zfs.img
# (btrfs: sudo mkfs.btrfs /var/lib/ulis-btrfs.img and mount it with -o loop,compress-force=zstd:3,noatime)
```

Every write then goes through two filesystems: expect noticeably slower writes and fsyncs (the bulk load) and somewhat
slower cold reads; ZFS documents file vdevs as meant for testing. It still pays when the disk is the limit.

**Checking the ratio.** ZFS: `zfs get compressratio,logicalused,used ulis/data/postgres`. btrfs:
`sudo compsize /srv/ulis/postgres`. Either: `du -sh --apparent-size /srv/ulis/postgres` against `du -sh /srv/ulis/postgres`.

**What not to do.**

* Keep `fsync=on`, the default `synchronous_commit` and ZFS's default `sync=standard`: turning any of them off loses
  committed transactions on a power cut (`fsync=off` can corrupt the whole cluster).
* Do not set `full_page_writes=off`, on btrfs least of all, unless you have checked that the filesystem can never tear an
  8 KB page in your exact configuration; it saves WAL, not space (`wal_compression=on` is already set and lossless).
* No ZFS deduplication (needs a lot of RAM for little gain here) and no high zstd levels on the live database (write
  latency); `compression=zstd` (level 3) is the setting measured above.
* Do not `chattr +C` on btrfs (see above), and do not expect more from compressing the dumps or the `.zst` objects again.
