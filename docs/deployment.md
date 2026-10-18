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
| Bulk loads (e.g. all German federal laws, more countries) | 8 vCPU / 16 GB / 200 GB | raise `PG_SHARED_BUFFERS` to 25% of RAM |
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
  SHA-256). After a crash, reboot or Ctrl-C run the same command again; cached files are not fetched twice.
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

* `./backup.sh` writes `ulis-<utc>.dump` (pg_dump, compressed, checksummed, read back to verify) into
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
| disk full | cached downloads of ingested sources can be deleted (`/srv/ulis/sources`; raw copies are in `objects`); `docker system prune` |

## 12. Files

`deploy/Dockerfile` (multi-stage, non-root, healthcheck), `docker-compose.yml`, `Caddyfile`, `.env.example`,
`backup.sh`, `restore.sh`, `export-corpus.sh`, `import-corpus.sh`, `lib.sh`, `systemd/`; code in `ulis/api/security.py`
and `ulis/ops/`. The root `docker-compose.yml` is a development database only (`docker compose up db`); there is no second API image.
Tested on: Python 3.13, PostgreSQL 16.15 clients/server, Starlette/uvicorn as pinned in the venv.
