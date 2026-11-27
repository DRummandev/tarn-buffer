"""Fetch helpers, Baghdad time, normalisation, number parsing, tokenize/eval."""

from __future__ import annotations

import html
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.error import HTTPError, URLError

from .model import SANITY_MAX, SANITY_MIN

BAGHDAD = timezone(timedelta(hours=3), name="Asia/Baghdad")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LOC_LABEL = {
    "kifah": "Baghdad",
    "harithiya": "Baghdad",
    "samawal": "Baghdad",
    "baghdad": "Baghdad",
    "erbil": "Erbil",
    "sulaymaniyah": "Sulaymaniyah",
    "duhok": "Duhok",
    "basra": "Basra",
    "najaf": "Najaf",
    "karbala": "Karbala",
    "kirkuk": "Kirkuk",
    "mosul": "Mosul",
    "babil": "Babil",
    "diyala": "Diyala",
    "anbar": "Anbar",
    "wasit": "Wasit",
    "qadisiyah": "Qadisiyah",
    "parallel": "Baghdad",
    "shops": "Baghdad",
    "shop": "Baghdad",
}

ALIAS = {
    "كفاح": "kifah",
    "حارثيه": "harithiya",
    "سموال": "samawal",
    "بغداد": "baghdad",
    "اربيل": "erbil",
    "سليمانيه": "sulaymaniyah",
    "دهوك": "duhok",
    "بصره": "basra",
    "نجف": "najaf",
    "كربلاء": "karbala",
    "كربلا": "karbala",
    "كركوك": "kirkuk",
    "موصل": "mosul",
    "بابل": "babil",
    "ديالي": "diyala",
    "انبار": "anbar",
    "واسط": "wasit",
    "قادسيه": "qadisiyah",
}

FX_WORDS = {
    "يورو",
    "تومان",
    "ليره",
    "دينار",
    "درهم",
    "ريال",
    "كيلو",
    "فضه",
    "اونصه",
    "ذهب",
}
QUAL_WORDS = {"باجر", "توقعات", "توقع", "تقريبي", "انباع"}

_TAG_RE = re.compile(r"(?s)<script.*?</script>|<style.*?</style>")
_BR_RE = re.compile(r"(?i)<br\s*/?>")
_ANY_TAG = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Letter runs must NOT swallow digits (e.g. حارثية169,500) — mirror Go \p{L}+.
_TOK_RE = re.compile(r"\d[\d,.]*\d|\d|[^\W\d_]+", re.UNICODE)
_THOU_RE = re.compile(r"^\d{1,3}([,.]\d{3})+$")
_SIX_RE = re.compile(r"^\d{6}$")


def now_baghdad() -> datetime:
    return datetime.now(BAGHDAD)


def rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BAGHDAD)
    return dt.astimezone(BAGHDAD).isoformat()


def parse_baghdad_local(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BAGHDAD)


def clip(s: str, n: int = 200) -> str:
    if len(s) <= n:
        return s
    return s[:n] + "…"


def norm(s: str) -> str:
    out: list[str] = []
    for r in s:
        o = ord(r)
        if "٠" <= r <= "٩":
            out.append(chr(ord("0") + (o - ord("٠"))))
        elif "۰" <= r <= "۹":
            out.append(chr(ord("0") + (o - ord("۰"))))
        elif r in ("٬", "،"):
            out.append(",")
        elif r == "٫":
            out.append(".")
        elif r in ("أ", "إ", "آ", "ٱ"):
            out.append("ا")
        elif r == "ة":
            out.append("ه")
        elif r == "ى":
            out.append("ي")
        elif r == "ـ" or 0x064B <= o <= 0x0652 or o in (
            0x200E,
            0x200F,
            0x202A,
            0x202B,
            0x202C,
            0x2066,
            0x2067,
            0x2068,
            0x2069,
        ):
            continue
        else:
            out.append(r)
    return "".join(out)


def html_to_text(s: str) -> str:
    s = _TAG_RE.sub("", s)
    s = _BR_RE.sub(" ", s)
    s = _ANY_TAG.sub(" ", s)
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def num_val(s: str) -> Optional[int]:
    if _THOU_RE.match(s) or _SIX_RE.match(s):
        try:
            return int(s.replace(",", "").replace(".", ""))
        except ValueError:
            return None
    return None


def strip_prefixes(w: str) -> list[str]:
    c = [w]
    if w.startswith("و"):
        c.append(w[1:])
    for x in list(c):
        if x.startswith("ال"):
            c.append(x[2:])
    return c


def market_of(w: str) -> str:
    for c in strip_prefixes(w):
        m = ALIAS.get(c)
        if m:
            return m
    return ""


@dataclass
class Tok:
    k: str  # m n b a c w
    s: str
    mk: str = ""
    v: int = 0


def tokenize(text: str) -> list[Tok]:
    out: list[Tok] = []
    for t in _TOK_RE.findall(norm(text)):
        if t[0].isdigit():
            v = num_val(t)
            if v is not None and v >= 10_000:
                out.append(Tok(k="n", s=t, v=v))
            continue
        if t == "مطلوب":
            out.append(Tok(k="b", s=t))
        elif t in ("معروض", "عرض"):
            out.append(Tok(k="a", s=t))
        elif t == "و":
            out.append(Tok(k="c", s=t))
        else:
            mk = market_of(t)
            if mk:
                out.append(Tok(k="m", s=t, mk=mk))
            else:
                out.append(Tok(k="w", s=t))
    return out


@dataclass
class Hit:
    market: str
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    status: str = "ok"
    warnings: list[str] | None = None

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []


def in_range(v: float, lo: int = SANITY_MIN, hi: int = SANITY_MAX) -> bool:
    return lo <= v <= hi


def _kw_between(sp: list[Tok], kws: list[int], a: int, b: int) -> bool:
    return any(a < k < b for k in kws)


def eval_span(
    sp: list[Tok],
    *,
    list_mode: bool = False,
    lo: int = SANITY_MIN,
    hi: int = SANITY_MAX,
) -> tuple[float, float, float, str, list[str]]:
    nums: list[int] = []
    kws: list[int] = []
    for i, t in enumerate(sp):
        if t.k == "n":
            nums.append(i)
        elif t.k in ("b", "a") and not list_mode:
            kws.append(i)
    if not nums:
        return 0.0, 0.0, 0.0, "no_price", []

    status = "ok"
    bid = ask = mid = 0.0
    warns: list[str] = []

    if list_mode:
        mid = float(sp[nums[0]].v)
        ask = mid
    elif kws:
        used: dict[int, bool] = {}
        for ki in kws:
            got = -1
            for ni in nums:
                if ni > ki and not used.get(ni) and not _kw_between(sp, kws, ki, ni):
                    got = ni
                    break
            if got < 0:
                for ni in reversed(nums):
                    if ni < ki and not used.get(ni) and not _kw_between(sp, kws, ni, ki):
                        got = ni
                        break
            if got < 0:
                return 0.0, 0.0, 0.0, "ambiguous", ["keyword_without_number"]
            used[got] = True
            if sp[ki].k == "b":
                bid = float(sp[got].v)
            else:
                ask = float(sp[got].v)
        if len(used) != len(nums):
            return bid, ask, 0.0, "ambiguous", ["extra_numbers"]
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        elif bid > 0:
            mid = bid
            warns.append("bid_only")
        else:
            mid = ask
            warns.append("ask_only")
    elif len(nums) == 1:
        mid = float(sp[nums[0]].v)
    elif len(nums) == 2:
        bid = float(sp[nums[0]].v)
        ask = float(sp[nums[1]].v)
        mid = (bid + ask) / 2
        warns.append("two_numbers_assumed_bid_ask")
        if ask < bid:
            return bid, ask, mid, "ambiguous", warns
    else:
        return 0.0, 0.0, 0.0, "ambiguous", ["many_numbers"]

    for v in (bid, ask, mid):
        if v > 0 and not in_range(v, lo, hi):
            return bid, ask, mid, "out_of_range", warns
    if bid > 0 and ask > 0:
        if ask < bid:
            return bid, ask, mid, "crossed", warns
        if mid and (ask - bid) / mid * 100 > 1:
            return bid, ask, mid, "wide_spread", warns
    return bid, ask, mid, status, warns


def parse_text_core(
    text: str,
    *,
    list_mode: bool = False,
    lo: int = SANITY_MIN,
    hi: int = SANITY_MAX,
) -> tuple[list[Hit], str]:
    """Port of Go ParseText."""
    n = norm(text)
    low = n.lower()
    if not text.strip():
        return [], "no_text"
    if "whatsapp" in low or "t.me/" in low or "http" in low:
        return [], "promo_skipped"

    toks = tokenize(text)
    if not list_mode:
        for t in toks:
            if t.k == "w":
                for c in strip_prefixes(t.s):
                    if c in FX_WORDS:
                        return [], "fx_skipped"
                    if c in QUAL_WORDS:
                        return [], "qualifier_skipped"

    groups: list[tuple[list[str], int, int]] = []
    i = 0
    while i < len(toks):
        if toks[i].k != "m":
            i += 1
            continue
        start = i
        mks = [toks[i].mk]
        j = i
        while j + 1 < len(toks) and (
            toks[j + 1].k == "m"
            or (toks[j + 1].k == "c" and j + 2 < len(toks) and toks[j + 2].k == "m")
        ):
            j += 1
            if toks[j].k == "c":
                continue
            mks.append(toks[j].mk)
        groups.append((mks, start, j))
        i = j + 1

    if not groups:
        return [], "not_a_rate"
    if not any(t.k == "n" for t in toks):
        return [], "not_a_rate"

    hits: list[Hit] = []
    for gi, (mks, _start, end) in enumerate(groups):
        end_span = groups[gi + 1][1] if gi + 1 < len(groups) else len(toks)
        span = toks[end + 1 : end_span]
        bid, ask, mid, st, w = eval_span(span, list_mode=list_mode, lo=lo, hi=hi)
        if st == "no_price" and gi == 0:
            bid, ask, mid, st, w = eval_span(
                toks[: groups[0][1]], list_mode=list_mode, lo=lo, hi=hi
            )
            if st != "no_price":
                w = list(w) + ["number_before_name"]
        if len(mks) > 1:
            w = list(w) + ["combined_post"]
        for mk in mks:
            hits.append(
                Hit(market=mk, bid=bid, ask=ask, mid=mid, status=st, warnings=list(w))
            )
    return hits, "rate_post"


def classify_http_error(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        code = exc.code
        if code == 403:
            return "http_403"
        if code == 429:
            return "http_429"
        if code == 404:
            return "http_404"
        return f"http_{code}"
    if isinstance(exc, socket.timeout) or isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ssl.SSLError):
        return "ssl"
    if isinstance(exc, URLError):
        reason = str(getattr(exc, "reason", exc))
        low = reason.lower()
        if "timed out" in low or "timeout" in low:
            return "timeout"
        if "name or service not known" in low or "nodename" in low or "getaddrinfo" in low:
            return "dns"
        if "ssl" in low or "certificate" in low:
            return "ssl"
        return "network"
    if isinstance(exc, OSError):
        return "network"
    return "unknown"


def fetch(url: str, *, timeout: float = 25.0, retry_after: float = 3.0) -> tuple[int, bytes, int, str]:
    """Desktop Chrome UA fetch with one retry. Never raises for HTTP/network errors.

    Returns (status, body_bytes, ms, error_class). error_class empty on success.
    """
    headers = {
        "User-Agent": UA,
        "Accept-Language": "ar,en",
        "Accept": "text/html,application/json,*/*",
    }
    last_err = ""
    status = 0
    body = b""
    ms = 0
    for attempt in range(2):
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode() or 200
                body = resp.read()
            ms = int((time.monotonic() - t0) * 1000)
            if not body:
                last_err = "empty_body"
                if attempt == 0:
                    time.sleep(retry_after)
                    continue
                return status, body, ms, last_err
            return status, body, ms, ""
        except Exception as e:  # noqa: BLE001 — classify, never raise
            ms = int((time.monotonic() - t0) * 1000)
            last_err = classify_http_error(e)
            if isinstance(e, HTTPError):
                status = e.code
                try:
                    body = e.read() or b""
                except Exception:  # noqa: BLE001
                    body = b""
            if attempt == 0:
                time.sleep(retry_after)
                continue
            return status, body, ms, last_err
    return status, body, ms, last_err or "unknown"
