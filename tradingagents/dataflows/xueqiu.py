"""Xueqiu (雪球) timeline fetcher for Chinese A-share sentiment.

Xueqiu is the largest Chinese investing community — broadly the Chinese
analogue of StockTwits combined with Seeking Alpha. Posts are indexed by
stock symbol with the convention ``SH600519`` (Shanghai) or ``SZ000001``
(Shenzhen). The public timeline endpoint at
``xueqiu.com/statuses/stock_timeline.json`` returns recent posts mentioning
the symbol with text body, post time, and engagement metrics (retweets,
replies, favorites). It requires an anonymous session cookie that any
browser request gets for free; we obtain it on a preflight GET.

No API key required. Returns a formatted plaintext block ready for prompt
injection and degrades gracefully — returns a placeholder string rather
than raising — so callers never special-case missing data.
"""

from __future__ import annotations

import http.cookiejar
import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from tradingagents.dataflows.symbol_utils import china_ticker_parts

logger = logging.getLogger(__name__)

_HOME = "https://xueqiu.com"
_API = "https://xueqiu.com/statuses/stock_timeline.json?{qs}"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _strip_html(text: str) -> str:
    """Reduce the small amount of HTML Xueqiu embeds in post bodies to plain text."""
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    return " ".join(cleaned.split())


def fetch_xueqiu_posts(ticker: str, limit: int = 20, timeout: float = 10.0) -> str:
    """Fetch recent Xueqiu posts mentioning ``ticker`` and return a formatted
    plaintext block ready for prompt injection.

    Only Chinese A-share tickers (``.SS`` / ``.SZ``) are supported; other
    tickers return an explanatory placeholder so the caller can stay generic.
    """
    parts = china_ticker_parts(ticker)
    if parts is None:
        return f"<Xueqiu only covers Chinese A-shares; {ticker} is not an A-share symbol>"

    prefix, code = parts
    xq_symbol = f"{prefix.upper()}{code}"

    qs = urlencode({
        "symbol_id": xq_symbol,
        "symbol": xq_symbol,
        "count": limit,
        "source": "all",
    })
    url = _API.format(qs=qs)

    # Xueqiu requires an anonymous session cookie before serving JSON; a
    # preflight GET to the home page is enough to obtain one.
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{_HOME}/S/{xq_symbol}",
    }

    try:
        opener.open(Request(_HOME, headers={"User-Agent": _UA}), timeout=timeout).read()
        with opener.open(Request(url, headers=headers), timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning("Xueqiu fetch failed for %s: %s", ticker, exc)
        return f"<xueqiu unavailable: {type(exc).__name__}>"

    posts = data.get("list", []) if isinstance(data, dict) else []
    if not posts:
        return f"<no Xueqiu posts found for {xq_symbol}>"

    lines = []
    for p in posts[:limit]:
        created_at = p.get("created_at") or p.get("timeBefore") or "?"
        user = (p.get("user") or {}).get("screen_name") or "?"
        retweets = p.get("retweet_count") or 0
        replies = p.get("reply_count") or 0
        favorites = p.get("fav_count") or 0
        body = _strip_html(p.get("text") or p.get("description") or "")
        if len(body) > 280:
            body = body[:280] + "…"
        lines.append(
            f"[{created_at} · @{user} · {retweets:>3}↻ · {replies:>3}c · {favorites:>3}★] {body}"
        )

    header = (
        f"Xueqiu — {len(lines)} recent posts for {xq_symbol} "
        f"(retweets ↻ / replies c / favorites ★):"
    )
    return header + "\n" + "\n".join(lines)
