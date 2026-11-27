"""Row schema and shared status constants."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


UNIT = "IQD_per_100_USD"
SANITY_MIN = 120_000
SANITY_MAX = 220_000

# parse_status values (rows)
STATUS_OK = "ok"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_OUT_OF_RANGE = "out_of_range"
STATUS_CROSSED = "crossed"
STATUS_WIDE_SPREAD = "wide_spread"
STATUS_NO_PRICE = "no_price"
STATUS_PROMO = "promo_skipped"
STATUS_FX = "fx_skipped"
STATUS_QUALIFIER = "qualifier_skipped"
STATUS_NOT_A_RATE = "not_a_rate"
STATUS_NO_TEXT = "no_text"

MARKETS = (
    "kifah",
    "harithiya",
    "samawal",
    "baghdad",
    "erbil",
    "sulaymaniyah",
    "duhok",
    "basra",
    "najaf",
    "karbala",
    "kirkuk",
    "mosul",
    "babil",
    "diyala",
    "anbar",
    "wasit",
    "qadisiyah",
    "parallel",
    "shops",
    "shop",
)


class SourceShapeError(Exception):
    """Payload does not match the known source shape; whole source failed."""


@dataclass
class Row:
    source: str
    fetched_at: str
    market: str = ""
    location: str = ""
    unit: str = UNIT
    parse_status: str = STATUS_OK
    warnings: list[str] = field(default_factory=list)
    post_id: str = ""
    post_time: str = ""
    kind: str = ""
    label: str = ""
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # drop empty optionals for compact JSONL (keep zeros that are meaningful)
        out: dict[str, Any] = {}
        for k, v in d.items():
            if v is None or v == "" or v == []:
                continue
            out[k] = v
        # always keep required-ish fields
        for k in ("source", "fetched_at", "market", "location", "unit", "parse_status"):
            out[k] = d[k]
        if d["warnings"]:
            out["warnings"] = d["warnings"]
        if d["text"]:
            out["text"] = d["text"]
        for k in ("bid", "ask", "mid"):
            if d[k] is not None and d[k] != 0:
                out[k] = d[k]
            elif d[k] == 0 and k in ("bid", "ask") and d["parse_status"] == STATUS_OK:
                # omit zero bid/ask when unused
                pass
        # include mid even when equal to bid for audit when status ok / non-ok with mid
        if d["mid"] is not None and d["mid"] != 0:
            out["mid"] = d["mid"]
        for k in ("post_id", "post_time", "kind", "label"):
            if d[k]:
                out[k] = d[k]
        return out
