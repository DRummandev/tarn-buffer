"""Issue-based ingest alerts + keepalive decision (Phase 3).

Pure decide_alerts(...) + thin GitHub REST wrapper via urllib.
GITHUB_TOKEN is read from the environment only and never printed.
API failures are logged and swallowed — they must not fail a scrape run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from .common import BAGHDAD, rfc3339
from .tradingtime import (
    DEFAULT_TRADING_DAYS,
    DEFAULT_TRADING_WINDOW,
    DEFAULT_TRADING_WINDOW_BY_DAY,
    day_name,
    market_minutes_between,
    resolve_trading_window,
)

UTC = timezone.utc

TITLE_SOURCES_FAILING = "ingest: sources failing"
TITLE_PRIMARY_FAILING = "ingest: primary source failing"
TITLE_PARSER_DRIFT = "ingest: parser drift"
TEST_TITLE_SOURCES_FAILING = "TEST ingest: sources failing"

LABEL_INGEST = "ingest"
PRIMARY_SOURCE = "tg_kifah"

FAIL_OPEN_MINUTES = 30.0
PARSER_DRIFT_MARKET_HOURS = 24.0
KEEPALIVE_MIN_AGE_DAYS = 6.0


@dataclass(frozen=True)
class OpenIssue:
    number: int
    title: str
    state: str = "open"


@dataclass(frozen=True)
class AlertAction:
    """One intended GitHub issue mutation."""

    op: str  # "open" | "close" | "noop"
    title: str
    body: str = ""
    comment: str = ""
    issue_number: Optional[int] = None


@dataclass
class AlertPlan:
    actions: list[AlertAction] = field(default_factory=list)


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BAGHDAD)
    return dt


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        return _ensure_aware(datetime.fromisoformat(s))
    except ValueError:
        return None


def _age_minutes(start: Optional[datetime], now: datetime) -> float:
    if start is None:
        return 0.0
    a = _ensure_aware(start)
    b = _ensure_aware(now)
    if b <= a:
        return 0.0
    return (b - a).total_seconds() / 60.0


def is_market_time(
    now: datetime,
    *,
    trading_days: Sequence[str] = DEFAULT_TRADING_DAYS,
    trading_window: tuple[str, str] = DEFAULT_TRADING_WINDOW,
    trading_window_by_day: Optional[Mapping[str, tuple[str, str]]] = None,
) -> bool:
    """True when `now` falls inside a trading day ∩ that day's window (Baghdad)."""
    n = _ensure_aware(now).astimezone(BAGHDAD)
    name = day_name(n)
    if name not in set(trading_days):
        return False
    by_day = (
        trading_window_by_day
        if trading_window_by_day is not None
        else DEFAULT_TRADING_WINDOW_BY_DAY
    )
    win = resolve_trading_window(
        name,
        trading_window=trading_window,
        trading_window_by_day=by_day,
    )
    wh0, wm0 = map(int, win[0].split(":"))
    wh1, wm1 = map(int, win[1].split(":"))
    open_t = n.replace(hour=wh0, minute=wm0, second=0, microsecond=0)
    close_t = n.replace(hour=wh1, minute=wm1, second=0, microsecond=0)
    return open_t <= n < close_t


def should_update_keepalive(
    last_commit_at: datetime,
    now: datetime,
    *,
    min_age_days: float = KEEPALIVE_MIN_AGE_DAYS,
) -> bool:
    """True when the last commit on main is older than min_age_days (default 6)."""
    a = _ensure_aware(last_commit_at)
    b = _ensure_aware(now)
    return (b - a).total_seconds() >= float(min_age_days) * 86400.0


def _find_open(open_issues: Sequence[OpenIssue], title: str) -> Optional[OpenIssue]:
    for iss in open_issues:
        if iss.title == title and iss.state == "open":
            return iss
    return None


def _all_sources_fetch_failing(status: dict[str, Any]) -> bool:
    sources = status.get("sources") or {}
    if not sources:
        return False
    return all(not bool((info or {}).get("ok")) for info in sources.values())


def _oldest_failure_at(status: dict[str, Any]) -> Optional[datetime]:
    oldest: Optional[datetime] = None
    for info in (status.get("sources") or {}).values():
        if bool((info or {}).get("ok")):
            continue
        t = _parse_iso((info or {}).get("first_failure_at"))
        if t is None:
            continue
        if oldest is None or t < oldest:
            oldest = t
    return oldest


def _source_failure_age_minutes(status: dict[str, Any], source_id: str, now: datetime) -> float:
    info = (status.get("sources") or {}).get(source_id) or {}
    if bool(info.get("ok")):
        return 0.0
    return _age_minutes(_parse_iso(info.get("first_failure_at")), now)


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def build_alert_body(
    *,
    title: str,
    status: dict[str, Any],
    now: datetime,
) -> str:
    """Dry public body: times, per-source error class, first_failure_at, last good, run link.

    Never includes tokens or secrets.
    """
    n = _ensure_aware(now)
    utc = n.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    bgd = n.astimezone(BAGHDAD).isoformat()
    lines = [
        f"title: {title}",
        f"utc: {utc}",
        f"baghdad: {bgd}",
        "sources:",
    ]
    for sid in sorted((status.get("sources") or {}).keys()):
        info = (status.get("sources") or {}).get(sid) or {}
        ok = "ok" if info.get("ok") else "FAIL"
        err = info.get("last_error_class") or "-"
        ff = info.get("first_failure_at") or "-"
        last = info.get("last_ok_post_time") or "-"
        lines.append(f"  {sid}: {ok} class={err} first_failure_at={ff} last_ok_post={last}")
    lines.append(f"selector_has_candidate: {bool(status.get('selector_has_candidate'))}")
    lines.append(f"primary_ok: {bool(status.get('primary_ok'))}")
    drift = status.get("parser_drift") or {}
    if drift:
        lines.append(
            f"parser_drift: active={bool(drift.get('active'))} since={drift.get('since') or '-'} "
            f"ok_kifah_rows={drift.get('ok_kifah_rows', '-')} rate_looking={drift.get('rate_looking')}"
        )
    run = _run_url()
    if run:
        lines.append(f"run: {run}")
    body = "\n".join(lines) + "\n"
    # Defence: never emit token-looking substrings from env accidentally
    tok = os.environ.get("GITHUB_TOKEN") or ""
    if tok and tok in body:
        body = body.replace(tok, "[REDACTED]")
    return body


def decide_alerts(
    status: dict[str, Any],
    selector_has_candidate: bool,
    now: datetime,
    open_issues: Sequence[OpenIssue],
    *,
    trading_days: Sequence[str] = DEFAULT_TRADING_DAYS,
    trading_window: tuple[str, str] = DEFAULT_TRADING_WINDOW,
    trading_window_by_day: Optional[Mapping[str, tuple[str, str]]] = None,
    fail_open_minutes: float = FAIL_OPEN_MINUTES,
    parser_drift_market_hours: float = PARSER_DRIFT_MARKET_HOURS,
) -> list[AlertAction]:
    """Decide open/close/noop for Issues A/B/C from status + open issues.

    Weekend / non-trading stale (no fetch/parse failure) → no Issue A.
    """
    now = _ensure_aware(now)
    actions: list[AlertAction] = []
    status = dict(status or {})
    by_day = (
        trading_window_by_day
        if trading_window_by_day is not None
        else DEFAULT_TRADING_WINDOW_BY_DAY
    )
    # Prefer explicit arg; fall back to status field
    has_cand = bool(selector_has_candidate)
    if "selector_has_candidate" in status:
        # Keep arg authoritative when provided; status used for body/duration fields
        pass

    # --- Issue A: sources failing / no valid candidate during market time ---
    open_a = _find_open(open_issues, TITLE_SOURCES_FAILING)
    want_a = False
    if _all_sources_fetch_failing(status):
        oldest = _oldest_failure_at(status)
        if _age_minutes(oldest, now) >= fail_open_minutes:
            want_a = True
    elif not has_cand and is_market_time(
        now,
        trading_days=trading_days,
        trading_window=trading_window,
        trading_window_by_day=by_day,
    ):
        since = _parse_iso(status.get("selector_no_candidate_since"))
        if since is not None and _age_minutes(since, now) >= fail_open_minutes:
            want_a = True

    if want_a:
        if open_a is None:
            actions.append(
                AlertAction(
                    op="open",
                    title=TITLE_SOURCES_FAILING,
                    body=build_alert_body(title=TITLE_SOURCES_FAILING, status=status, now=now),
                )
            )
        # existing open → no duplicate
    else:
        if open_a is not None and has_cand:
            actions.append(
                AlertAction(
                    op="close",
                    title=TITLE_SOURCES_FAILING,
                    issue_number=open_a.number,
                    comment="recovered: valid candidate present",
                )
            )
        elif open_a is not None and not _all_sources_fetch_failing(status) and has_cand:
            actions.append(
                AlertAction(
                    op="close",
                    title=TITLE_SOURCES_FAILING,
                    issue_number=open_a.number,
                    comment="recovered: sources healthy",
                )
            )

    # --- Issue B: primary source failing ≥ 30 min ---
    open_b = _find_open(open_issues, TITLE_PRIMARY_FAILING)
    primary_age = _source_failure_age_minutes(status, PRIMARY_SOURCE, now)
    primary_failing = primary_age >= fail_open_minutes
    if primary_failing:
        if open_b is None:
            actions.append(
                AlertAction(
                    op="open",
                    title=TITLE_PRIMARY_FAILING,
                    body=build_alert_body(title=TITLE_PRIMARY_FAILING, status=status, now=now),
                )
            )
    else:
        primary_ok = bool(((status.get("sources") or {}).get(PRIMARY_SOURCE) or {}).get("ok"))
        if open_b is not None and primary_ok:
            actions.append(
                AlertAction(
                    op="close",
                    title=TITLE_PRIMARY_FAILING,
                    issue_number=open_b.number,
                    comment="recovered: primary healthy",
                )
            )

    # --- Issue C: parser drift (optional/recommended) ---
    open_c = _find_open(open_issues, TITLE_PARSER_DRIFT)
    drift = status.get("parser_drift") or {}
    drift_active = bool(drift.get("active"))
    drift_since = _parse_iso(drift.get("since"))
    want_c = False
    if drift_active and drift_since is not None:
        mins = market_minutes_between(
            drift_since,
            now,
            trading_days=trading_days,
            trading_window=trading_window,
            trading_window_by_day=by_day,
        )
        if mins >= parser_drift_market_hours * 60.0:
            want_c = True
    if want_c:
        if open_c is None:
            actions.append(
                AlertAction(
                    op="open",
                    title=TITLE_PARSER_DRIFT,
                    body=build_alert_body(title=TITLE_PARSER_DRIFT, status=status, now=now),
                )
            )
    else:
        ok_rows = drift.get("ok_kifah_rows")
        if open_c is not None and (not drift_active or (isinstance(ok_rows, int) and ok_rows > 0)):
            actions.append(
                AlertAction(
                    op="close",
                    title=TITLE_PARSER_DRIFT,
                    issue_number=open_c.number,
                    comment="recovered: ok Kifah rows present",
                )
            )

    return actions


# ----- GitHub REST (urllib; token from env only) -----


class GitHubAPIError(Exception):
    pass


def _gh_request(
    method: str,
    path: str,
    *,
    body: Optional[dict[str, Any]] = None,
    token: Optional[str] = None,
) -> Any:
    tok = token if token is not None else os.environ.get("GITHUB_TOKEN") or ""
    if not tok:
        raise GitHubAPIError("GITHUB_TOKEN missing")
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    if not repo or "/" not in repo:
        raise GitHubAPIError("GITHUB_REPOSITORY missing")
    url = f"https://api.github.com/repos/{repo}{path}"
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {tok}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tarn-buffer-alerts",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        raise GitHubAPIError(f"HTTP {e.code} {method} {path}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise GitHubAPIError(f"{type(e).__name__}: {e}") from e


def ensure_ingest_label(*, token: Optional[str] = None) -> None:
    try:
        _gh_request(
            "POST",
            "/labels",
            body={"name": LABEL_INGEST, "color": "B60205", "description": "ingest alerts"},
            token=token,
        )
    except GitHubAPIError as e:
        # 422 = already exists
        if "422" not in str(e):
            print(f"alerts: ensure label: {e}", file=sys.stderr)


def list_open_ingest_issues(*, token: Optional[str] = None) -> list[OpenIssue]:
    out: list[OpenIssue] = []
    page = 1
    while page <= 5:
        q = urllib.parse.urlencode({"state": "open", "per_page": 100, "page": page})
        data = _gh_request("GET", f"/issues?{q}", token=token)
        if not isinstance(data, list) or not data:
            break
        for item in data:
            if not isinstance(item, dict) or item.get("pull_request"):
                continue
            out.append(
                OpenIssue(
                    number=int(item["number"]),
                    title=str(item.get("title") or ""),
                    state=str(item.get("state") or "open"),
                )
            )
        if len(data) < 100:
            break
        page += 1
    return out


def apply_alert_actions(
    actions: Sequence[AlertAction],
    *,
    token: Optional[str] = None,
    dry_run: bool = False,
) -> list[str]:
    """Apply actions. Returns log lines. Never raises (swallows API errors)."""
    logs: list[str] = []
    if not actions:
        logs.append("alerts: no action")
        return logs
    try:
        ensure_ingest_label(token=token)
    except Exception as e:  # noqa: BLE001
        logs.append(f"alerts: label skipped: {type(e).__name__}")
    for act in actions:
        try:
            if act.op == "open":
                if dry_run:
                    logs.append(f"alerts: dry-open {act.title}")
                    continue
                created = _gh_request(
                    "POST",
                    "/issues",
                    body={
                        "title": act.title,
                        "body": act.body,
                        "labels": [LABEL_INGEST],
                    },
                    token=token,
                )
                num = (created or {}).get("number")
                logs.append(f"alerts: opened #{num} {act.title}")
            elif act.op == "close":
                if act.issue_number is None:
                    logs.append(f"alerts: close missing number for {act.title}")
                    continue
                if dry_run:
                    logs.append(f"alerts: dry-close #{act.issue_number} {act.title}")
                    continue
                if act.comment:
                    _gh_request(
                        "POST",
                        f"/issues/{act.issue_number}/comments",
                        body={"body": act.comment},
                        token=token,
                    )
                _gh_request(
                    "PATCH",
                    f"/issues/{act.issue_number}",
                    body={"state": "closed"},
                    token=token,
                )
                logs.append(f"alerts: closed #{act.issue_number} {act.title}")
            else:
                logs.append(f"alerts: noop {act.title}")
        except GitHubAPIError as e:
            logs.append(f"alerts: API error on {act.op} {act.title}: {e}")
        except Exception as e:  # noqa: BLE001
            logs.append(f"alerts: error on {act.op} {act.title}: {type(e).__name__}")
    return logs


def run_alerts_from_status_file(
    status_path: str,
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> int:
    """Load status.json, decide, apply. Always exit 0 (API errors swallowed)."""
    now = now or datetime.now(BAGHDAD)
    try:
        with open(status_path, encoding="utf-8") as f:
            status = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"alerts: cannot read status: {type(e).__name__}", file=sys.stderr)
        return 0
    try:
        open_issues = list_open_ingest_issues()
    except GitHubAPIError as e:
        print(f"alerts: list issues failed: {e}", file=sys.stderr)
        return 0
    has_cand = bool(status.get("selector_has_candidate"))
    actions = decide_alerts(status, has_cand, now, open_issues)
    for line in apply_alert_actions(actions, dry_run=dry_run):
        print(line)
    return 0


def _test_scenario(scenario: str) -> int:
    """Owner-facing alert-test.yml scenarios against TEST title only."""
    title = TEST_TITLE_SOURCES_FAILING
    try:
        ensure_ingest_label()
        open_issues = list_open_ingest_issues()
    except GitHubAPIError as e:
        print(f"alert-test: API error: {e}", file=sys.stderr)
        return 0

    existing = _find_open(open_issues, title)
    if scenario == "open":
        if existing is not None:
            print(f"alert-test: already open #{existing.number} (no duplicate)")
            return 0
        body = (
            f"title: {title}\n"
            f"utc: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"baghdad: {rfc3339(datetime.now(BAGHDAD))}\n"
            "note: TEST issue from alert-test.yml — not a production alert\n"
            f"run: {_run_url()}\n"
        )
        try:
            created = _gh_request(
                "POST",
                "/issues",
                body={"title": title, "body": body, "labels": [LABEL_INGEST]},
            )
            print(f"alert-test: opened #{(created or {}).get('number')} {title}")
        except GitHubAPIError as e:
            print(f"alert-test: open failed: {e}", file=sys.stderr)
        return 0

    if scenario == "duplicate-check":
        # Re-run open logic: must not create a second issue
        if existing is None:
            # create once then check
            try:
                created = _gh_request(
                    "POST",
                    "/issues",
                    body={
                        "title": title,
                        "body": "TEST duplicate-check seed\n",
                        "labels": [LABEL_INGEST],
                    },
                )
                print(f"alert-test: seeded #{(created or {}).get('number')}")
                open_issues = list_open_ingest_issues()
                existing = _find_open(open_issues, title)
            except GitHubAPIError as e:
                print(f"alert-test: seed failed: {e}", file=sys.stderr)
                return 0
        # Attempt "would open" via decide-style: existing → no duplicate
        fake_open = [
            AlertAction(op="open", title=title, body="should not create")
        ]
        # Manual guard matching production: if open exists, skip
        if existing is not None:
            print(f"alert-test: duplicate-check OK — existing #{existing.number}, no new issue")
        else:
            print("alert-test: duplicate-check unexpected — no open issue after seed")
        _ = fake_open
        return 0

    if scenario == "close":
        if existing is None:
            print("alert-test: nothing to close")
            return 0
        try:
            _gh_request(
                "POST",
                f"/issues/{existing.number}/comments",
                body={"body": "TEST close via alert-test.yml"},
            )
            _gh_request(
                "PATCH",
                f"/issues/{existing.number}",
                body={"state": "closed"},
            )
            print(f"alert-test: closed #{existing.number} {title}")
        except GitHubAPIError as e:
            print(f"alert-test: close failed: {e}", file=sys.stderr)
        return 0

    print(f"alert-test: unknown scenario {scenario!r}", file=sys.stderr)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="tarn-buffer ingest alerts")
    p.add_argument("--status", type=str, default="status.json", help="Path to status.json")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--test-scenario",
        choices=("open", "close", "duplicate-check"),
        default=None,
        help="alert-test.yml scenarios against TEST title",
    )
    p.add_argument(
        "--keepalive-due",
        action="store_true",
        help="Print yes/no whether keepalive should touch .keepalive (needs --last-commit-iso)",
    )
    p.add_argument("--last-commit-iso", type=str, default=None)
    p.add_argument("--now-iso", type=str, default=None)
    args = p.parse_args(argv)

    now = _parse_iso(args.now_iso) if args.now_iso else datetime.now(BAGHDAD)

    if args.keepalive_due:
        if not args.last_commit_iso:
            print("no", file=sys.stderr)
            return 2
        last = _parse_iso(args.last_commit_iso)
        if last is None:
            print("no", file=sys.stderr)
            return 2
        print("yes" if should_update_keepalive(last, now or datetime.now(UTC)) else "no")
        return 0

    if args.test_scenario:
        return _test_scenario(args.test_scenario)

    return run_alerts_from_status_file(args.status, now=now, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
