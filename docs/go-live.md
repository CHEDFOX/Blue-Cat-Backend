# Go-live: laws in force of nine countries

The first release loads the laws in force of the US (federal), Canada (federal), Australia (federal), the UK, Germany, Spain,
Sweden, Finland and Japan. Court decisions, old editions, the states and provinces that are only partly covered, and every
other country load on request (see `config/launch-markets.yaml` and `docs/deployment.md` section 4). Every answer states the
date it was checked against; a date before the data starts is reported as "cannot be checked", never answered from today's text.

## What `load-all` loads by default (`load-all --dry-run`)

| Source | Country | Documents | Network time (lower bound) |
|---|---|---|---|
| `us-ecfr` | US | 50 titles of regulations | 4 min |
| `us-uscode-house` | US | 53 titles of the Code | 5 min |
| `ca-justice-laws-xml` | Canada | 1,906 Acts and regulations (git copy) | minutes |
| `au-federal-register` | Australia | 785 | 2 h 10 min |
| `se-riksdagen-sfs` | Sweden | 5,336 | 7 h 25 min |
| `de-gesetze-im-internet` | Germany | 6,139 federal laws | 8 h 30 min |
| `es-boe-consolidada` | Spain | 6,826 State norms in force | 9 h 30 min |
| `gb-legislation-gov-uk` | UK | 7,178 Acts and similar, every version, repealed ones included | 10 h |
| `jp-egov-laws` | Japan | 8,989 | 12 h 30 min |
| `fi-finlex` | Finland | 11,158 requests | 15 h 30 min |
| `gb-legislation-gov-uk-secondary` | UK | 17,878 revised statutory instruments | 25 h |

About 90 hours of waiting one source after the other. `--jobs 6` loads several at once, never two from the same website, so the
UK pair (same host) is the longest path at about 35 hours; allow **two days** for the first fill including parsing and indexing.
After that, `load-all --refresh` (weekly timer) fetches only what changed.

## Size (estimates from slices, plus or minus 50 percent; not measured on a full load)

Database about **40-60 GB** (UK about 15, Japan 4-8, Spain 8-12, Finland 2-3, Australia 2, Sweden 0.5, the rest a few GB each);
raw downloads and cache about the same again. Provide **200 GB** of disk, 8 vCPU and 16 GB of RAM (the bulk-load row of `docs/deployment.md` section 1); raise `PG_SHARED_BUFFERS` to 25% of RAM.

## Steps on the server

1. Set up the server as in `docs/deployment.md` sections 1-3 (Docker, `deploy/.env` from `deploy/.env.example`: `ULIS_DOMAIN`,
   `ULIS_ACME_EMAIL`, `POSTGRES_PASSWORD`, `ULIS_API_KEYS`, and `ANTHROPIC_API_KEY` for the research assistant), then
   `docker compose up -d --build` in `/opt/ulis/deploy` and check `/ready`.
2. `docker compose --profile tools run --rm cli load-all --dry-run` (the table above), then
   `docker compose --profile tools run -d --name ulis-load cli load-all --jobs 6`; follow it with `docker logs -f ulis-load`.
3. When it ends: `... cli status` and `... cli verify-sources` (every stored provision is re-derived from the raw bytes).
4. Check `GET /v1/coverage` (needs an API key): per country, what is loaded and what the profile says is missing. Publish that page.
5. Run the evaluation questions (`eval/questions.yaml`) and read the failures before inviting testers.

## Before taking money

* The licence readings in `docs/licence-review.md` are the project's own, not counsel's. Have a lawyer check them.
* Set `ULIS_COMMERCIAL_USE=1`: the gate then refuses every source whose commercial use is not allowed (all nine countries above pass).
* The Docker/Caddy/systemd setup and the real model have not been run end to end; expect small fixes on the first deploy.

## Not in this release

India (its law sites block the bot), Ireland's updated texts, Italy (commercial use unknown), the states and provinces listed under
`not_yet`, the EU, France, the Netherlands and Latvia (slow or still to review), case law everywhere, and every country not named above.
