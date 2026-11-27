"""Live read-only probe CLI (used by probe.yml). Never commits."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .common import BAGHDAD, fetch, now_baghdad, rfc3339
from .dinarlive import parse_dinarlive
from .dollariraqi import parse_dollariraqi_page
from .info4iq import parse_info4iq
from .iraqsm import parse_iraqsm
from .kifah import parse_kifah_page
from .model import Row, SourceShapeError

SOURCES = {
    "tg_kifah": "https://t.me/s/borsat_alkfah",
    "tg_dollariraqi": "https://t.me/s/dollariraqi",
    "info4iq": "https://info4iq.com/dollar/get_prem_markets_prices.php",
    "iraqsm": "https://iraqsm.com/data/fx.json",
    "dinarlive_erbil": "https://dinarlive.com/api/v2/get-price?id=5&location=erbil",
}

TG_EMBED = {
    "tg_kifah": "https://t.me/s/borsat_alkfah?embed=1",
    "tg_dollariraqi": "https://t.me/s/dollariraqi?embed=1",
}

RAW_CAP_BYTES = 20 * 1024 * 1024


@dataclass
class SourceStats:
    cycles: int = 0
    fetch_ok: int = 0
    parse_ok: int = 0
    http_hist: dict[str, int] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    latencies: list[int] = field(default_factory=list)
    newest_post_time: str = ""
    newest_value: Optional[float] = None
    last_cands: dict[str, Any] = field(default_factory=dict)
    warn_examples: list[str] = field(default_factory=list)


def _row_key(r: Row) -> str:
    return "|".join(
        [
            r.source,
            r.post_id,
            r.market,
            r.parse_status,
            str(r.bid or ""),
            str(r.ask or ""),
            str(r.mid or ""),
        ]
    )


def _best_cand(rows: list[Row], source: str, card: str) -> Optional[dict[str, Any]]:
    """Pick best ok candidate for baghdad/erbil cards per brief rules."""
    want: list[tuple[str, str, str]] = []  # market, kind, source filter already applied
    if card == "baghdad":
        if source == "tg_kifah":
            want = [("kifah", "bursa")]
        elif source == "tg_dollariraqi":
            want = [("baghdad", "bursa")]
        elif source == "info4iq":
            want = [("kifah", "bursa")]
        elif source == "iraqsm":
            want = [("parallel", "bursa")]
        else:
            return None
    elif card == "erbil":
        if source == "tg_kifah":
            want = [("erbil", "bursa")]
        elif source == "tg_dollariraqi":
            want = [("erbil", "bursa")]
        elif source == "dinarlive_erbil":
            want = [("erbil", "bursa")]
        else:
            return None
    else:
        return None

    ok = [
        r
        for r in rows
        if r.parse_status == "ok"
        and any(r.market == m and (not k or r.kind == k) for m, k in want)
    ]
    if not ok:
        return None
    # newest post_time wins; else first
    def key(r: Row) -> str:
        return r.post_time or ""

    best = sorted(ok, key=key, reverse=True)[0]
    return {
        "market": best.market,
        "mid": best.mid,
        "bid": best.bid,
        "ask": best.ask,
        "post_time": best.post_time,
        "post_id": best.post_id,
    }


def _parse_source(name: str, body: bytes, fetched_at: datetime) -> tuple[list[Row], int, str]:
    """Returns rows, n_posts, error_class (empty if ok)."""
    try:
        if name == "tg_kifah":
            rows, n = parse_kifah_page(body.decode("utf-8", "replace"), fetched_at)
            return rows, n, ""
        if name == "tg_dollariraqi":
            rows, n = parse_dollariraqi_page(body.decode("utf-8", "replace"), fetched_at)
            return rows, n, ""
        if name == "info4iq":
            rows = parse_info4iq(body, fetched_at)
            return rows, len(rows), ""
        if name == "iraqsm":
            rows = parse_iraqsm(body, fetched_at)
            return rows, len(rows), ""
        if name == "dinarlive_erbil":
            rows = parse_dinarlive(body, fetched_at)
            return rows, len(rows), ""
        return [], 0, "unknown_source"
    except SourceShapeError as e:
        return [], 0, f"shape:{e}"
    except Exception as e:  # noqa: BLE001
        return [], 0, f"parse:{type(e).__name__}"


def _looks_captcha(body: bytes) -> bool:
    low = body[:8000].lower()
    return b"captcha" in low or b"cf-challenge" in low or b"attention required" in low


def _probe_one(
    name: str,
    url: str,
    out_dir: Path,
    cycle: int,
    raw_hashes: dict[str, str],
    raw_bytes_used: list[int],
) -> dict[str, Any]:
    fetched_at = now_baghdad()
    status, body, ms, err = fetch(url)
    attempts = 1
    # Telegram embed failover
    if name in TG_EMBED and (err or status != 200 or not body):
        status2, body2, ms2, err2 = fetch(TG_EMBED[name])
        attempts = 2
        if not err2 and body2:
            status, body, ms, err = status2, body2, ms2, err2
        else:
            ms = max(ms, ms2)
            err = err or err2

    fetch_ok = (not err) and status == 200 and bool(body)
    rows: list[Row] = []
    n_posts = 0
    parse_err = ""
    if fetch_ok:
        # try parse; for TG zero posts retry embed if not already
        rows, n_posts, parse_err = _parse_source(name, body, fetched_at)
        if name in TG_EMBED and (parse_err.startswith("shape:") or n_posts == 0) and attempts == 1:
            status2, body2, ms2, err2 = fetch(TG_EMBED[name])
            attempts = 2
            if not err2 and body2:
                status, body, ms, err = status2, body2, ms2, err2
                fetch_ok = status == 200 and bool(body)
                if fetch_ok:
                    rows, n_posts, parse_err = _parse_source(name, body, fetched_at)
                else:
                    parse_err = err or parse_err

    parse_ok = fetch_ok and not parse_err and any(r.parse_status == "ok" for r in rows)
    n_ok = sum(1 for r in rows if r.parse_status == "ok")
    status_counts: dict[str, int] = {}
    for r in rows:
        status_counts[r.parse_status] = status_counts.get(r.parse_status, 0) + 1

    latest_post = ""
    for r in rows:
        if r.post_time and r.post_time > latest_post:
            latest_post = r.post_time

    cands = {
        "baghdad": _best_cand(rows, name, "baghdad"),
        "erbil": _best_cand(rows, name, "erbil"),
    }

    # raw save policy
    h = hashlib.sha256(body).hexdigest() if body else ""
    save_raw = False
    if cycle == 0:
        save_raw = True
    elif not fetch_ok or parse_err:
        save_raw = True
    elif h and raw_hashes.get(name) != h:
        save_raw = True
    if save_raw and body and raw_bytes_used[0] < RAW_CAP_BYTES:
        raw_dir = out_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = raw_dir / f"{name}-{ts}.txt.gz"
        payload = gzip.compress(body)
        if raw_bytes_used[0] + len(payload) <= RAW_CAP_BYTES:
            path.write_bytes(payload)
            raw_bytes_used[0] += len(payload)
            if h:
                raw_hashes[name] = h

    error_class = err or parse_err
    if body and _looks_captcha(body):
        error_class = (error_class + "|captcha_looking") if error_class else "captcha_looking"

    return {
        "source": name,
        "fetched_at": rfc3339(fetched_at),
        "http": status,
        "ms": ms,
        "bytes": len(body),
        "attempts": attempts,
        "fetch_ok": fetch_ok,
        "parse_ok": bool(parse_ok),
        "error_class": error_class,
        "n_posts": n_posts,
        "n_rows": len(rows),
        "n_ok_rows": n_ok,
        "row_status_counts": status_counts,
        "latest_post_time": latest_post,
        "cands": cands,
        "_rows": rows,
    }


def _write_summary(stats: dict[str, SourceStats], path: Optional[Path]) -> str:
    lines = ["# Probe summary", ""]
    lines.append("| source | cycles | fetch ok % | parse ok % | median ms | max ms | newest post | newest mid |")
    lines.append("|---|---:|---:|---:|---:|---:|---|---:|")
    warn_lines: list[str] = []
    for name in SOURCES:
        s = stats[name]
        fo = (100.0 * s.fetch_ok / s.cycles) if s.cycles else 0.0
        po = (100.0 * s.parse_ok / s.cycles) if s.cycles else 0.0
        med = int(statistics.median(s.latencies)) if s.latencies else 0
        mx = max(s.latencies) if s.latencies else 0
        lines.append(
            f"| {name} | {s.cycles} | {fo:.0f}% | {po:.0f}% | {med} | {mx} | "
            f"{s.newest_post_time or '—'} | {s.newest_value if s.newest_value is not None else '—'} |"
        )
        http = ", ".join(f"{k}:{v}" for k, v in sorted(s.http_hist.items()))
        errs = ", ".join(f"{k}:{v}" for k, v in sorted(s.errors.items()))
        lines.append(f"  - HTTP: {http or '—'}")
        lines.append(f"  - errors: {errs or '—'}")
        if s.last_cands:
            lines.append(f"  - cands: `{json.dumps(s.last_cands, ensure_ascii=False)}`")
        for e in s.warn_examples[:5]:
            warn_lines.append(f"- {name}: {e}")

    lines.append("")
    lines.append("## Warnings / blocking?")
    if warn_lines:
        lines.extend(warn_lines)
    else:
        lines.append("- none flagged")
    text = "\n".join(lines) + "\n"
    if path:
        path.write_text(text, encoding="utf-8")
    summary_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_env:
        with open(summary_env, "a", encoding="utf-8") as f:
            f.write(text)
    return text


def run(duration_minutes: int, interval_seconds: int, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    cycles_path = out_dir / "cycles.jsonl"
    rows_path = out_dir / "rows.jsonl"
    seen_rows: set[str] = set()
    raw_hashes: dict[str, str] = {}
    raw_bytes_used = [0]
    stats = {n: SourceStats() for n in SOURCES}

    end = time.monotonic() + max(1, duration_minutes) * 60
    cycle = 0
    # Ensure at least one cycle
    while True:
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {
                ex.submit(
                    _probe_one, name, url, out_dir, cycle, raw_hashes, raw_bytes_used
                ): name
                for name, url in SOURCES.items()
            }
            for fut in as_completed(futs):
                results.append(fut.result())

        with cycles_path.open("a", encoding="utf-8") as cf, rows_path.open(
            "a", encoding="utf-8"
        ) as rf:
            for rec in results:
                rows: list[Row] = rec.pop("_rows", [])
                cf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                for r in rows:
                    k = _row_key(r)
                    if k in seen_rows:
                        continue
                    seen_rows.add(k)
                    rf.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
                    if r.parse_status != "ok" and len(stats[rec["source"]].warn_examples) < 8:
                        stats[rec["source"]].warn_examples.append(
                            f"{r.parse_status}: {r.text[:80]}"
                        )

                s = stats[rec["source"]]
                s.cycles += 1
                if rec["fetch_ok"]:
                    s.fetch_ok += 1
                if rec["parse_ok"]:
                    s.parse_ok += 1
                s.http_hist[str(rec["http"])] = s.http_hist.get(str(rec["http"]), 0) + 1
                if rec["error_class"]:
                    s.errors[rec["error_class"]] = s.errors.get(rec["error_class"], 0) + 1
                s.latencies.append(int(rec["ms"]))
                if rec["latest_post_time"] and rec["latest_post_time"] >= s.newest_post_time:
                    s.newest_post_time = rec["latest_post_time"]
                # Prefer baghdad card mid for summary value; else erbil.
                cands = rec.get("cands") or {}
                for card in ("baghdad", "erbil"):
                    c = cands.get(card)
                    if c and c.get("mid") is not None:
                        s.newest_value = c["mid"]
                        break
                s.last_cands = cands
                # blocking heuristics
                if rec["http"] in (403, 429) or rec["error_class"] in (
                    "empty_body",
                    "http_403",
                    "http_429",
                ) or "captcha" in (rec["error_class"] or ""):
                    s.warn_examples.append(
                        f"blocked from runner? http={rec['http']} err={rec['error_class']}"
                    )

        summary = _write_summary(stats, out_dir / "summary.md")
        print(summary, flush=True)

        cycle += 1
        if time.monotonic() >= end:
            break
        # sleep remaining interval, but stop if duration exhausted
        sleep_for = interval_seconds
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(sleep_for, remaining))

    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Live read-only source probe")
    p.add_argument("--duration-minutes", type=int, default=10)
    p.add_argument("--interval-seconds", type=int, default=300)
    p.add_argument("--out-dir", type=str, default="probe-out")
    args = p.parse_args(argv)
    duration = max(1, min(330, args.duration_minutes))
    interval = max(60, args.interval_seconds)
    return run(duration, interval, Path(args.out_dir))


if __name__ == "__main__":
    sys.exit(main())
