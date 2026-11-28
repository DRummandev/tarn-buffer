"""Load and validate config/sources.json. Invalid config aborts the run."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

KNOWN_SOURCES = (
    "tg_kifah",
    "tg_dollariraqi",
    "info4iq",
    "iraqsm",
    "dinarlive_erbil",
)

DAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


class ConfigError(Exception):
    """Invalid or unreadable sources.json — abort without writing."""


@dataclass(frozen=True)
class SourcesConfig:
    version: int
    baghdad_order: list[str]
    erbil_order: list[str]
    sanity_min: int
    sanity_max: int
    stale_trading_hours: float
    jump_pct: float
    confirm_tol_pct: float
    diff_warn_pct: float
    trading_days: list[str]
    trading_window: tuple[str, str]
    trading_window_by_day: dict[str, tuple[str, str]]
    enabled: dict[str, bool]

    def is_enabled(self, source_id: str) -> bool:
        return bool(self.enabled.get(source_id, False))


def _require(d: dict[str, Any], key: str) -> Any:
    if key not in d:
        raise ConfigError(f"missing key: {key}")
    return d[key]


def _as_float(v: Any, key: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{key} must be a number")
    return float(v)


def _as_int(v: Any, key: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
        raise ConfigError(f"{key} must be an integer")
    return int(v)


def _validate_order(order: Any, key: str) -> list[str]:
    if not isinstance(order, list) or not order:
        raise ConfigError(f"{key} must be a non-empty list")
    out: list[str] = []
    seen: set[str] = set()
    for item in order:
        if not isinstance(item, str) or not item:
            raise ConfigError(f"{key} entries must be non-empty strings")
        if item not in KNOWN_SOURCES:
            raise ConfigError(f"{key}: unknown source {item!r}")
        if item in seen:
            raise ConfigError(f"{key}: duplicate source {item!r}")
        seen.add(item)
        out.append(item)
    return out


def _validate_window(window: Any) -> tuple[str, str]:
    if not isinstance(window, list) or len(window) != 2:
        raise ConfigError("trading_window must be [start, end]")
    a, b = window
    if not isinstance(a, str) or not isinstance(b, str):
        raise ConfigError("trading_window entries must be HH:MM strings")
    for label, part in (("start", a), ("end", b)):
        try:
            hh, mm = part.split(":")
            h, m = int(hh), int(mm)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError as e:
            raise ConfigError(f"trading_window {label} invalid: {part!r}") from e
    return a, b


def validate_config_dict(raw: Any) -> SourcesConfig:
    if not isinstance(raw, dict):
        raise ConfigError("config root must be an object")
    version = _as_int(_require(raw, "version"), "version")
    baghdad = _validate_order(_require(raw, "baghdad_order"), "baghdad_order")
    erbil = _validate_order(_require(raw, "erbil_order"), "erbil_order")
    sanity_min = _as_int(_require(raw, "sanity_min"), "sanity_min")
    sanity_max = _as_int(_require(raw, "sanity_max"), "sanity_max")
    if sanity_min >= sanity_max:
        raise ConfigError("sanity_min must be < sanity_max")
    stale_h = _as_float(_require(raw, "stale_trading_hours"), "stale_trading_hours")
    jump_pct = _as_float(_require(raw, "jump_pct"), "jump_pct")
    confirm = _as_float(_require(raw, "confirm_tol_pct"), "confirm_tol_pct")
    diff_warn = _as_float(_require(raw, "diff_warn_pct"), "diff_warn_pct")
    days_raw = _require(raw, "trading_days")
    if not isinstance(days_raw, list) or not days_raw:
        raise ConfigError("trading_days must be a non-empty list")
    days: list[str] = []
    for d in days_raw:
        if d not in DAY_NAMES:
            raise ConfigError(f"trading_days: unknown day {d!r}")
        days.append(d)
    window = _validate_window(_require(raw, "trading_window"))
    by_day_raw = raw.get("trading_window_by_day") or {}
    if not isinstance(by_day_raw, dict):
        raise ConfigError("trading_window_by_day must be an object")
    by_day: dict[str, tuple[str, str]] = {}
    for day_name, day_win in by_day_raw.items():
        if day_name not in DAY_NAMES:
            raise ConfigError(f"trading_window_by_day: unknown day {day_name!r}")
        by_day[day_name] = _validate_window(day_win)
    enabled_raw = _require(raw, "enabled")
    if not isinstance(enabled_raw, dict):
        raise ConfigError("enabled must be an object")
    enabled: dict[str, bool] = {}
    for k, v in enabled_raw.items():
        if k not in KNOWN_SOURCES:
            raise ConfigError(f"enabled: unknown source {k!r}")
        if not isinstance(v, bool):
            raise ConfigError(f"enabled.{k} must be boolean")
        enabled[k] = v
    for src in baghdad + erbil:
        if src not in enabled:
            raise ConfigError(f"enabled missing entry for ordered source {src}")
    return SourcesConfig(
        version=version,
        baghdad_order=baghdad,
        erbil_order=erbil,
        sanity_min=sanity_min,
        sanity_max=sanity_max,
        stale_trading_hours=stale_h,
        jump_pct=jump_pct,
        confirm_tol_pct=confirm,
        diff_warn_pct=diff_warn,
        trading_days=days,
        trading_window=window,
        trading_window_by_day=by_day,
        enabled=enabled,
    )


def load_sources_config(path: Path | str) -> SourcesConfig:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read config: {e}") from e
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"invalid JSON: {e}") from e
    return validate_config_dict(raw)


def apply_overrides(cfg: SourcesConfig, overrides: Optional[dict[str, Any]]) -> SourcesConfig:
    """Merge scenario overrides onto a config (re-validated)."""
    if not overrides:
        return cfg
    base = {
        "version": cfg.version,
        "baghdad_order": list(cfg.baghdad_order),
        "erbil_order": list(cfg.erbil_order),
        "sanity_min": cfg.sanity_min,
        "sanity_max": cfg.sanity_max,
        "stale_trading_hours": cfg.stale_trading_hours,
        "jump_pct": cfg.jump_pct,
        "confirm_tol_pct": cfg.confirm_tol_pct,
        "diff_warn_pct": cfg.diff_warn_pct,
        "trading_days": list(cfg.trading_days),
        "trading_window": list(cfg.trading_window),
        "trading_window_by_day": {
            k: list(v) for k, v in cfg.trading_window_by_day.items()
        },
        "enabled": dict(cfg.enabled),
    }
    for k, v in overrides.items():
        if k == "enabled" and isinstance(v, dict):
            base["enabled"] = {**base["enabled"], **v}
        else:
            base[k] = v
    return validate_config_dict(base)


def default_config_path(repo_root: Optional[Path] = None) -> Path:
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / "config" / "sources.json"
