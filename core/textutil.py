"""Small text/URL/date helpers shared by all modules."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v ]+")
_BLOCK_TAGS = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|table|section)\b[^>]*>", re.I)

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gh_src", "gh_jid_src",
                   "src", "source", "ref", "refid", "trk", "trackingid", "lipi", "fbclid", "gclid", "mc_cid",
                   "mc_eid", "lever-source", "lever-origin", "ccuid", "cid", "tracking", "vjk", "from", "pos"}


def html_to_text(raw: str | None) -> str:
    """Convert HTML (possibly entity-escaped, as Greenhouse returns it) into readable plain text."""
    if not raw:
        return ""
    text = html.unescape(str(raw))
    if "<" in text and ">" in text:
        text = _BLOCK_TAGS.sub("\n", text)
        text = _TAG.sub(" ", text)
        text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def norm_text(s: str | None) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace. Used for comparison keys."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


_COMPANY_SUFFIX = re.compile(
    r"\b(pvt|private|ltd|limited|llp|llc|inc|incorporated|corp|corporation|co|company|plc|gmbh|"
    r"india|indian|bharat|technologies|technology|solutions|services|group|holdings|the)\b")


def norm_company(name: str | None) -> str:
    n = norm_text(name)
    n = _COMPANY_SUFFIX.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def canonical_url(url: str | None) -> str:
    """Strip tracking parameters, fragments, trailing slashes and www so equal links compare equal."""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    if not p.scheme or not p.netloc:
        return url.strip()
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False) if k.lower() not in TRACKING_PARAMS]
    host = p.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+$", "", p.path)
    return urlunparse(("https", host, path, "", urlencode(sorted(q)), ""))


def short_hash(*parts: str, length: int = 16) -> str:
    h = hashlib.sha1("|".join(p or "" for p in parts).encode("utf-8")).hexdigest()
    return h[:length]


def today_str(tz: str = "Asia/Kolkata") -> str:
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def to_iso_date(value) -> str:
    """Best-effort conversion of many date representations to YYYY-MM-DD ('' when unknown)."""
    if value in (None, "", "NaT"):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        if value != value:  # NaN
            return ""
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.utcfromtimestamp(seconds).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return ""
    s = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def fmt_num(x: float | None) -> str:
    """Number -> compact string ('8', '8.5'); None -> ''."""
    if x is None:
        return ""
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}".rstrip("0").rstrip(".")


def parse_num(s: str | None) -> float | None:
    if s is None or str(s).strip() == "":
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def safe_url(url: str | None, max_len: int = 2000) -> str:
    """Return ``url`` only if it is a plain http(s) URL with a host; otherwise "".

    Blocks javascript:, data:, file:, ftp: and similar schemes and strips control characters / spaces, so a hostile or
    malformed link scraped from a posting can never reach the sheet or the e-mail as a clickable link.
    """
    from urllib.parse import urlparse
    u = re.sub(r"[\x00-\x1f\x7f]", "", (url or "")).strip()
    if not u or len(u) > max_len or re.search(r"\s", u):
        return ""
    try:
        p = urlparse(u)
    except ValueError:
        return ""
    if p.scheme.lower() not in ("http", "https") or not p.netloc or "." not in p.netloc.split("@")[-1]:
        return ""
    return u
