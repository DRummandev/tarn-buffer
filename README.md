# tarn-buffer

Public static feed for **شكد سعر الدولار؟** — `latest.json`, `history/`, `status.json`.

## Feed URLs

- Primary: `https://raw.githubusercontent.com/DRummandev/tarn-buffer/main/latest.json`
- Fallback: `https://cdn.jsdelivr.net/gh/DRummandev/tarn-buffer@main/latest.json`
- History: same bases + `/history/index.json` and `/history/YYYY-MM-DD.jsonl`

## History JSONL (both shapes are intentional)

Each day file is append-only JSONL. **Do not rewrite past rows.** Writers use the compact form going forward; older lines may use the legacy form. Consumers (app data layer) must accept both.

Common fields: `t` (hold time, ISO +03:00), `p` (post time), `v` (shown value IQD/100 USD), `s` (shown source id), `m` (latest valid per-source values).

| Shape | `m` keys | `stale` |
| --- | --- | --- |
| **Legacy** (early rows) | Full ids: `tg_kifah`, `tg_dollariraqi`, `info4iq`, `iraqsm`, `dinarlive_erbil` | Always present (`true`/`false`) |
| **Compact** (current writer) | Short: `k`, `d`, `i`, `r`, `e` | Omitted when false; present only when `true` |

Short-key map: `k`←`tg_kifah`, `d`←`tg_dollariraqi`, `i`←`info4iq`, `r`←`iraqsm`, `e`←`dinarlive_erbil`.

Helpers: `scraper.outputs.parse_history_row` / `expand_history_m`.

## Scrape schedule

`.github/workflows/scrape.yml` runs on cron `*/5 * * * *` (every 5 minutes, UTC) on default branch `main`, plus `workflow_dispatch`. After a live push, jsDelivr purge is a best-effort **GET** to `https://purge.jsdelivr.net/gh/DRummandev/tarn-buffer@main/latest.json` (POST returns 405); the step logs `jsdelivr_purge_status=<code>`.
