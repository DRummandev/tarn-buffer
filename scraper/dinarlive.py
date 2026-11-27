"""dinarlive Erbil API parser."""

from __future__ import annotations

import json
from datetime import datetime

from .common import in_range, parse_baghdad_local, rfc3339
from .model import Row, SourceShapeError, UNIT


def parse_dinarlive(body_bytes: bytes, fetched_at: datetime) -> list[Row]:
    try:
        d = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise SourceShapeError(f"dinarlive: unexpected JSON: {e}") from e
    if not isinstance(d, dict) or "value" not in d:
        raise SourceShapeError("dinarlive: missing value")
    try:
        value = float(d["value"])
    except (TypeError, ValueError) as e:
        raise SourceShapeError(f"dinarlive: bad value: {e}") from e

    r = Row(
        source="dinarlive_erbil",
        fetched_at=rfc3339(fetched_at),
        unit=UNIT,
        market="erbil",
        location="Erbil",
        kind="bursa",
        mid=value,
    )
    created = d.get("created_at")
    if isinstance(created, str) and created:
        try:
            r.post_time = rfc3339(parse_baghdad_local(created))
            r.warnings.append("created_at_tz_assumed_baghdad")
        except ValueError:
            r.warnings.append("bad_time")
    else:
        r.warnings.append("bad_time")
    r.parse_status = "ok"
    if value <= 0:
        r.parse_status = "ambiguous"
    elif not in_range(value):
        r.parse_status = "out_of_range"
    return [r]
