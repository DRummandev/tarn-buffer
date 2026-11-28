# tarn-buffer

Public static feed for **شكد سعر الدولار؟** — `latest.json`, `history/`, `status.json`.

## Feed URLs

- Primary: `https://raw.githubusercontent.com/DRummandev/tarn-buffer/main/latest.json`
- Fallback: `https://cdn.jsdelivr.net/gh/DRummandev/tarn-buffer@main/latest.json`
- History: same bases + `/history/index.json` and `/history/YYYY-MM-DD.jsonl`
- OHLC: same bases + `/history/ohlc/index.json` and level files under `/history/ohlc/`

## Market hours (Asia/Baghdad, UTC+03:00)

- Trading days: **Sat–Thu**. **Friday closed**.
- Default window: **10:00–15:30**.
- Saturday window: **10:00–12:30** (from observed Kifah posts ~10:18–12:12; see `trading_window_by_day` in `config/sources.json` / feed `policy`).
- Official Baghdad / «بغداد طلب» rule: hero series = `market=kifah` + `kind=bursa` only (`tg_kifah`).

## `last_scrape_at`

Live scrapes set `last_scrape_at` (and `fetchedAt` / `generated_at`) on `latest.json`. The commit comparator ignores these heartbeat fields so a 5-minute cron does not spam empty commits when prices are unchanged.

## History JSONL (both shapes are intentional)

Each day file is append-only JSONL. **Do not rewrite past rows.** Writers use the compact form going forward; older lines may use the legacy form. Consumers (app data layer) must accept both.

Common fields: `t` (hold time, ISO +03:00), `p` (post time), `v` (shown value IQD/100 USD), `s` (shown source id), `m` (latest valid per-source values).

| Shape | `m` keys | `stale` |
| --- | --- | --- |
| **Legacy** (early rows) | Full ids: `tg_kifah`, `tg_dollariraqi`, `info4iq`, `iraqsm`, `dinarlive_erbil` | Always present (`true`/`false`) |
| **Compact** (current writer) | Short: `k`, `d`, `i`, `r`, `e` | Omitted when false; present only when `true` |

Short-key map: `k`←`tg_kifah`, `d`←`tg_dollariraqi`, `i`←`info4iq`, `r`←`iraqsm`, `e`←`dinarlive_erbil`.

Helpers: `scraper.outputs.parse_history_row` / `expand_history_m`.

## OHLC pre-aggregate (`history/ohlc/`)

Built by the scraper from daily jsonl (official Kifah prints only: history rows with `s=tg_kifah`). Never invents a close; empty hours are omitted (no forward-fill). Week starts **Saturday**. Chart consumers plot `c` (last print in the period).

| File | Shape |
| --- | --- |
| `index.json` | `levels`, `first`/`last` per level, `updated_at`, `series`, `week_starts` |
| `hourly.jsonl` | one OHLC record per Baghdad hour with ≥1 print |
| `daily.json` / `weekly.json` / `monthly.json` / `yearly.json` | JSON arrays of `{t,o,h,l,c,n}` (+ `incomplete` for the in-progress period) |

## Scrape schedule

`.github/workflows/scrape.yml` runs on cron `2-59/5 * * * *` (every 5 minutes, UTC) on default branch `main`, plus `workflow_dispatch`. After a live push, jsDelivr purge is a best-effort **GET** to `https://purge.jsdelivr.net/gh/DRummandev/tarn-buffer@main/latest.json` (POST returns 405); the step logs `jsdelivr_purge_status=<code>`.
