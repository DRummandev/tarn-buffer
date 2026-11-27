"""CLI: python -m scraper.run [--dry-run] [--out DIR] [--no-network --fixtures DIR]

Exit 0 even when sources fail (failure is data). Non-zero only on config/programming errors.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .cards import baghdad_candidates_from_rows, build_cards
from .common import fetch, now_baghdad, rfc3339
from .config import ConfigError, default_config_path, load_sources_config
from .dinarlive import parse_dinarlive
from .dollariraqi import parse_dollariraqi_page
from .info4iq import parse_info4iq
from .iraqsm import parse_iraqsm
from .kifah import parse_kifah_page
from .model import Row, SourceShapeError
from .outputs import (
    SOURCE_LABELS,
    append_history_if_changed,
    build_latest,
    build_status,
    card_states_from_latest,
    decision_table_markdown,
    history_row,
    latest_changed,
    load_json,
    make_quote,
    post_check_jump,
    post_url_for,
    status_changed,
    validate_latest_payload,
    write_json,
)
from .selector import Candidate, select_card, merge_card_state

SOURCES_URLS = {
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

FIXTURE_FILES = {
    "tg_kifah": "kifah_page.html",
    "tg_dollariraqi": "dollariraqi_page.html",
    "info4iq": "info4iq.json",
    "iraqsm": "iraqsm.json",
    "dinarlive_erbil": "dinarlive.json",
}


def _parse_source(name: str, body: bytes, fetched_at: datetime) -> tuple[list[Row], str]:
    try:
        if name == "tg_kifah":
            rows, _n = parse_kifah_page(body.decode("utf-8", "replace"), fetched_at)
            return rows, ""
        if name == "tg_dollariraqi":
            rows, _n = parse_dollariraqi_page(body.decode("utf-8", "replace"), fetched_at)
            return rows, ""
        if name == "info4iq":
            return parse_info4iq(body, fetched_at), ""
        if name == "iraqsm":
            return parse_iraqsm(body, fetched_at), ""
        if name == "dinarlive_erbil":
            return parse_dinarlive(body, fetched_at), ""
        return [], "unknown_source"
    except SourceShapeError as e:
        return [], f"shape:{e}"
    except Exception as e:  # noqa: BLE001
        return [], f"parse:{type(e).__name__}:{e}"


def _fetch_one(name: str, url: str) -> dict[str, Any]:
    fetched_at = now_baghdad()
    status, body, ms, err = fetch(url)
    if name in TG_EMBED and (err or status != 200 or not body):
        status2, body2, ms2, err2 = fetch(TG_EMBED[name])
        if not err2 and body2:
            status, body, ms, err = status2, body2, ms2, err2
        else:
            err = err or err2
            ms = max(ms, ms2)
    fetch_ok = (not err) and status == 200 and bool(body)
    rows: list[Row] = []
    parse_err = ""
    if fetch_ok:
        rows, parse_err = _parse_source(name, body, fetched_at)
        if name in TG_EMBED and parse_err.startswith("shape:"):
            status2, body2, ms2, err2 = fetch(TG_EMBED[name])
            if not err2 and body2:
                status, body, ms, err = status2, body2, ms2, err2
                fetch_ok = status == 200 and bool(body)
                if fetch_ok:
                    rows, parse_err = _parse_source(name, body, fetched_at)
    ok = fetch_ok and not parse_err
    last_ok_post = ""
    if ok:
        for r in rows:
            if r.parse_status == "ok" and r.post_time and r.post_time > last_ok_post:
                last_ok_post = r.post_time
    return {
        "source": name,
        "fetch_ok": ok,
        "error_class": None if ok else (parse_err or err or "unknown"),
        "rows": rows,
        "http_status": status,
        "ms": ms,
        "last_ok_post_time": last_ok_post or None,
        "ok": ok,
    }


def _load_fixtures(fixtures_dir: Path, fetched_at: datetime) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, fname in FIXTURE_FILES.items():
        path = fixtures_dir / fname
        if not path.exists():
            # try live variants
            alt = fixtures_dir / fname.replace(".json", "_live.json")
            path = alt if alt.exists() else path
        if not path.exists():
            out[name] = {
                "source": name,
                "fetch_ok": False,
                "error_class": "fixture_missing",
                "rows": [],
                "ok": False,
                "last_ok_post_time": None,
            }
            continue
        body = path.read_bytes()
        rows, parse_err = _parse_source(name, body, fetched_at)
        ok = not parse_err
        last_ok_post = ""
        if ok:
            for r in rows:
                if r.parse_status == "ok" and r.post_time and r.post_time > last_ok_post:
                    last_ok_post = r.post_time
        out[name] = {
            "source": name,
            "fetch_ok": ok,
            "error_class": None if ok else parse_err,
            "rows": rows,
            "ok": ok,
            "last_ok_post_time": last_ok_post or None,
        }
    return out


def _fetch_all_parallel() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(_fetch_one, n, u): n for n, u in SOURCES_URLS.items()}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                out[name] = fut.result()
            except Exception as e:  # noqa: BLE001
                out[name] = {
                    "source": name,
                    "fetch_ok": False,
                    "error_class": f"worker:{type(e).__name__}",
                    "rows": [],
                    "ok": False,
                    "last_ok_post_time": None,
                }
    return out


def _quote_from_rows(
    sid: str,
    info: dict[str, Any],
    *,
    market: Optional[str] = None,
    kind: Optional[str] = None,
    credit: Optional[str] = None,
    market_label: str = "",
) -> Optional[dict[str, Any]]:
    """Build a fresh quote from this run's ok rows, if any."""
    if not info.get("fetch_ok"):
        return None
    rows: list[Row] = list(info.get("rows") or [])
    ok_rows = [
        r
        for r in rows
        if r.parse_status == "ok"
        and r.mid is not None
        and (market is None or r.market == market)
        and (kind is None or r.kind == kind)
    ]
    if not ok_rows:
        return None
    r = sorted(ok_rows, key=lambda x: x.post_time or "", reverse=True)[0]
    bid, ask = r.bid, r.ask
    if bid is not None and ask is not None and bid == ask:
        bid = ask = None
    elif bid is None and ask is not None and r.mid == ask:
        bid = ask = None
    return make_quote(
        source=sid,
        value=float(r.mid),
        bid=bid,
        ask=ask,
        posted_at=r.post_time,
        post_id=r.post_id or "",
        post_url=post_url_for(sid, r.post_id or ""),
        fetch_ok=True,
        error_class=None,
        warnings=list(r.warnings or []),
        credit=credit,
        source_label=SOURCE_LABELS.get(sid, sid),
        market_label=market_label or SOURCE_LABELS.get(sid, sid),
    )


def _sources_quotes(
    by_source: dict[str, dict[str, Any]],
    baghdad_cands: dict[str, Candidate],
    prev_latest: Optional[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Every source that currently or previously produced a valid value."""
    prev_sources = (prev_latest or {}).get("sources") or {}
    out: dict[str, dict[str, Any]] = {}

    prefer_market = {
        "tg_kifah": ("kifah", "bursa", None, "الكفاح"),
        "tg_dollariraqi": ("baghdad", "bursa", None, "بغداد"),
        "info4iq": ("kifah", None, None, "الكفاح"),
        "iraqsm": ("parallel", "bursa", "Data: IQWealth (iraqsm.com)", "الموازي"),
        "dinarlive_erbil": ("erbil", None, None, "أربيل"),
    }

    for sid in ("tg_kifah", "tg_dollariraqi", "info4iq", "iraqsm", "dinarlive_erbil"):
        info = by_source.get(sid) or {}
        prev_q = prev_sources.get(sid)
        cand = baghdad_cands.get(sid)

        q = None
        if cand and cand.parse_ok and cand.value is not None and cand.posted_at and info.get("fetch_ok"):
            q = make_quote(
                source=sid,
                value=float(cand.value),
                bid=cand.bid,
                ask=cand.ask,
                posted_at=cand.posted_at,
                post_id=cand.post_id,
                post_url=cand.post_url or post_url_for(sid, cand.post_id),
                stale=False,
                stale_since=None,
                fetch_ok=True,
                error_class=None,
                warnings=list(cand.warnings),
                credit=cand.credit,
                source_label=cand.source_label,
                market_label=cand.market_label,
            )
        else:
            mk, kind, credit, mlabel = prefer_market[sid]
            q = _quote_from_rows(
                sid, info, market=mk, kind=kind, credit=credit, market_label=mlabel
            )

        if q is not None:
            out[sid] = q
        elif prev_q and isinstance(prev_q, dict) and prev_q.get("price") is not None:
            held = dict(prev_q)
            held["fetch_ok"] = False
            held["error_class"] = info.get("error_class") or held.get("error_class") or "fetch_error"
            out[sid] = held
    return out


def _source_value_map(quotes: dict[str, dict[str, Any]]) -> dict[str, float]:
    m: dict[str, float] = {}
    for sid, q in quotes.items():
        if not isinstance(q, dict):
            continue
        v = q.get("value")
        if v is None:
            v = q.get("per100")
        if v is not None and q.get("price") is not None:
            m[sid] = float(v)
    return m


def run_pipeline(
    *,
    repo_root: Path,
    dry_run: bool = False,
    out_dir: Optional[Path] = None,
    no_network: bool = False,
    fixtures_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
    by_source: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    cfg = load_sources_config(default_config_path(repo_root))
    now = now or now_baghdad()

    if by_source is None:
        if no_network:
            if fixtures_dir is None:
                fixtures_dir = repo_root / "tests" / "fixtures"
            by_source = _load_fixtures(fixtures_dir, now)
        else:
            by_source = _fetch_all_parallel()

    latest_path = repo_root / "latest.json"
    history_dir = repo_root / "history"
    status_path = repo_root / "status.json"

    if dry_run:
        dest = out_dir or (repo_root / "dry-out")
        dest.mkdir(parents=True, exist_ok=True)
        write_latest = dest / "latest.json"
        write_history = dest / "history"
        write_status = dest / "status.json"
    else:
        write_latest = latest_path
        write_history = history_dir
        write_status = status_path

    prev_latest = load_json(latest_path)
    prev_status = load_json(status_path)
    prev_states = card_states_from_latest(prev_latest)

    baghdad_cands = baghdad_candidates_from_rows(by_source)
    baghdad_dec = select_card(
        "baghdad",
        baghdad_cands,
        prev_states.get("baghdad"),
        now,
        cfg,
        order=list(cfg.baghdad_order),
    )
    cards, card_states, _card_decs = build_cards(
        by_source=by_source,
        prev_states=prev_states,
        now=now,
        cfg=cfg,
    )
    # persist baghdad state into card_states conceptually
    bag_state = merge_card_state(prev_states.get("baghdad"), baghdad_dec, rfc3339(now))

    quotes = _sources_quotes(by_source, baghdad_cands, prev_latest)
    # Mark source quotes stale using selector candidate why_not when present
    for sid, q in quotes.items():
        meta = (baghdad_dec.candidates or {}).get(sid) or {}
        if meta.get("why_not") == "stale":
            q["stale"] = True
            q["stale_since"] = q.get("posted_at")
        if not (by_source.get(sid) or {}).get("fetch_ok"):
            q["fetch_ok"] = False
            if q.get("error_class") is None:
                q["error_class"] = (by_source.get(sid) or {}).get("error_class")

    latest = build_latest(
        now=now,
        cfg=cfg,
        baghdad=baghdad_dec,
        sources_quotes=quotes,
        cards=cards,
    )

    source_results = {
        sid: {
            "ok": bool(info.get("ok")),
            "error_class": info.get("error_class"),
            "last_ok_post_time": info.get("last_ok_post_time"),
            "ms": info.get("ms"),
            "newest_value": _newest_ok_value(info),
        }
        for sid, info in by_source.items()
    }
    parser_drift = _detect_parser_drift(by_source.get("tg_kifah") or {})
    status = build_status(
        now=now,
        prev_status=prev_status,
        source_results=source_results,
        baghdad=baghdad_dec,
        parser_drift=parser_drift,
    )

    # Compare against committed latest.json (dry-run never treats prior dry-out as baseline)
    changed_latest = latest_changed(prev_latest, latest)
    would_change_status = status_changed(prev_status, status)

    wrote_latest = False
    wrote_history = False
    wrote_status = False
    post_check_failed = False
    validation_error: Optional[str] = None

    # Pre-commit validation + jump post-check (defence in depth)
    if changed_latest:
        validation_error = validate_latest_payload(
            latest, sanity_min=cfg.sanity_min, sanity_max=cfg.sanity_max
        )
        if validation_error is None:
            jump_err = post_check_jump(prev_latest, latest, jump_pct=cfg.jump_pct)
            if jump_err:
                validation_error = jump_err
        if validation_error:
            post_check_failed = True
            # Commit nothing: skip all writes (failure recorded in logs / result)
            changed_latest = False
            would_change_status = False

    if changed_latest and not post_check_failed:
        write_json(write_latest, latest, pretty=True)
        wrote_latest = True
        if baghdad_dec.ok and baghdad_dec.value is not None:
            row = history_row(
                t=now,
                baghdad=baghdad_dec,
                source_values=_source_value_map(quotes),
            )
            wrote_history = append_history_if_changed(write_history, row, now=now)
        elif dry_run:
            write_history.mkdir(parents=True, exist_ok=True)

    if would_change_status and not post_check_failed:
        write_json(write_status, status, pretty=True)
        wrote_status = True
    elif dry_run and not post_check_failed:
        # Always materialise status in dry-out for the artifact
        write_json(write_status, status, pretty=True)

    table = decision_table_markdown(
        baghdad=baghdad_dec,
        cards=cards,
        source_results=source_results,
    )
    compact_lines = compact_run_log(
        by_source=by_source,
        source_results=source_results,
        baghdad=baghdad_dec,
        changed_latest=changed_latest,
        post_check_failed=post_check_failed,
        validation_error=validation_error,
    )

    result = {
        "ok": True,
        "dry_run": dry_run,
        "changed_latest": changed_latest,
        "wrote_latest": wrote_latest,
        "wrote_history": wrote_history,
        "wrote_status": wrote_status,
        "post_check_failed": post_check_failed,
        "validation_error": validation_error,
        "decision": baghdad_dec.decision,
        "value": baghdad_dec.value,
        "source": baghdad_dec.source,
        "warnings": list(baghdad_dec.warnings),
        "table": table,
        "compact_log": compact_lines,
        "latest": latest,
        "status": status,
        "bag_state": bag_state,
        "by_source": by_source,
    }
    return result


def _newest_ok_value(info: dict[str, Any]) -> Optional[float]:
    rows: list[Row] = list(info.get("rows") or [])
    best: Optional[float] = None
    best_t = ""
    for r in rows:
        if r.parse_status == "ok" and r.mid is not None and r.post_time:
            if r.post_time >= best_t:
                best_t = r.post_time
                best = float(r.mid)
    return best


def _detect_parser_drift(kifah_info: dict[str, Any]) -> dict[str, Any]:
    """Primary fetched OK with posts, but zero ok Kifah rows while rate-looking posts exist."""
    rows: list[Row] = list(kifah_info.get("rows") or [])
    http_status = kifah_info.get("http_status")
    http_ok = bool(kifah_info.get("fetch_ok") or (http_status == 200 and rows))
    # fetch_ok in run means parse succeeded overall; for drift we need HTTP/posts present
    if http_status is not None:
        http_ok = http_status == 200 and bool(rows)
    elif rows and not kifah_info.get("error_class"):
        http_ok = True
    ok_kifah = sum(
        1 for r in rows if r.market == "kifah" and r.parse_status == "ok" and r.mid is not None
    )
    rate_looking_statuses = {
        "ok",
        "ambiguous",
        "out_of_range",
        "crossed",
        "wide_spread",
        "not_a_rate",
        "bid_only",
        "ask_only",
    }
    rate_looking = any(
        (r.parse_status in rate_looking_statuses) or (r.market and r.parse_status != "no_text")
        for r in rows
    )
    # Also count posts that look like rates via non-empty market hits that failed
    if not rate_looking and rows:
        rate_looking = any(r.market for r in rows)
    active = bool(http_ok and ok_kifah == 0 and rate_looking and rows)
    return {
        "active": active,
        "ok_kifah_rows": ok_kifah,
        "rate_looking": rate_looking,
        "http_ok": http_ok,
    }


def compact_run_log(
    *,
    by_source: dict[str, dict[str, Any]],
    source_results: dict[str, dict[str, Any]],
    baghdad: Any,
    changed_latest: bool,
    post_check_failed: bool = False,
    validation_error: Optional[str] = None,
) -> list[str]:
    """One compact line per source + decision line."""
    lines: list[str] = []
    for sid in ("tg_kifah", "tg_dollariraqi", "info4iq", "iraqsm", "dinarlive_erbil"):
        info = by_source.get(sid) or {}
        sr = source_results.get(sid) or {}
        ok = bool(info.get("ok"))
        flag = "ok" if ok else "FAIL"
        err = "-" if ok else (info.get("error_class") or sr.get("error_class") or "unknown")
        ms = info.get("ms")
        latency = f"{ms}ms" if ms is not None else "-"
        newest_v = sr.get("newest_value")
        newest_s = str(int(newest_v)) if isinstance(newest_v, float) and newest_v == int(newest_v) else (
            str(newest_v) if newest_v is not None else "-"
        )
        newest_t = info.get("last_ok_post_time") or "-"
        lines.append(f"{sid} {flag} {err} {latency} {newest_s} {newest_t}")
    held = getattr(baghdad, "held_reason", None) or "-"
    decision = getattr(baghdad, "decision", "-")
    value = getattr(baghdad, "value", None)
    source = getattr(baghdad, "source", None) or "-"
    extra = ""
    if post_check_failed:
        extra = f" post_check_failed={validation_error or 'post_check_failed'}"
    lines.append(
        f"decision {decision} value={value} source={source} held={held} "
        f"changed={str(changed_latest).lower()}{extra}"
    )
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="tarn-buffer scraper run")
    p.add_argument("--dry-run", action="store_true", help="Write to --out, never touch repo outputs")
    p.add_argument("--out", type=str, default=None, help="Dry-run output directory (default dry-out/)")
    p.add_argument("--no-network", action="store_true", help="Use fixture snapshots instead of network")
    p.add_argument("--fixtures", type=str, default=None, help="Fixtures directory")
    p.add_argument("--repo-root", type=str, default=None, help="Repository root")
    args = p.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else Path(__file__).resolve().parent.parent
    out_dir = Path(args.out) if args.out else None
    fixtures = Path(args.fixtures) if args.fixtures else None

    try:
        result = run_pipeline(
            repo_root=repo_root,
            dry_run=args.dry_run,
            out_dir=out_dir,
            no_network=args.no_network,
            fixtures_dir=fixtures,
        )
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    # Compact logs only (one line per source + decision) — keep noise low for Actions
    for line in result.get("compact_log") or []:
        print(line, flush=True)
    if result.get("post_check_failed"):
        print(f"post_check_failed {result.get('validation_error')}", flush=True)
    if args.dry_run and result.get("latest") and (repo_root / "latest.json").exists():
        prev = load_json(repo_root / "latest.json")
        print(
            f"dry_diff_changed={latest_changed(prev, result['latest'])}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
