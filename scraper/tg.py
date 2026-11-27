"""Telegram widget HTML → posts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .common import BAGHDAD, html_to_text

_DATA_POST_RE = re.compile(r'data-post="([^"]+)"')
_TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
_TEXT_RE = re.compile(r"(?s)js-message_text[^>]*>(.*?)</div>")


@dataclass
class TGPost:
    id: str
    time: datetime
    text: str
    has_text: bool = True


def parse_tg(body: str) -> list[TGPost]:
    parts = body.split("tgme_widget_message_wrap")
    out: list[TGPost] = []
    for b in parts[1:]:
        post_id = ""
        m = _DATA_POST_RE.search(b)
        if m:
            post_id = m.group(1)
        t: Optional[datetime] = None
        ms = _TIME_RE.findall(b)
        if ms:
            raw = ms[-1]
            try:
                # RFC3339 / ISO
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                t = dt.astimezone(BAGHDAD)
            except ValueError:
                t = None
        text = ""
        has_t = False
        tm = _TEXT_RE.search(b)
        if tm:
            text = html_to_text(tm.group(1))
            has_t = True
        if not post_id or t is None:
            continue
        out.append(TGPost(id=post_id, time=t, text=text, has_text=has_t))
    return out


def post_id_tail(data_post: str) -> str:
    """borsat_alkfah/30588 → 30588 or keep full."""
    if "/" in data_post:
        return data_post
    return data_post
