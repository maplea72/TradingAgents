"""Sina Finance (新浪财经) news fetcher for Chinese A-share sentiment.

Sina Finance is one of the largest Chinese-language financial news portals.
It exposes a per-symbol news roll at
``vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/<sym>.phtml``
that lists headlines, publication dates, and article URLs. The page is
returned as GB18030-encoded HTML; we parse it with a small set of regexes
rather than a full HTML library to keep the dependency surface flat (the
project already avoids ``beautifulsoup4`` for the same reason in other
fetchers).

The Chinese-site convention is ``sh600519`` / ``sz000001`` (lowercase
exchange prefix concatenated with the numeric code). We derive that from
the Yahoo-style ``.SS`` / ``.SZ`` ticker via :func:`china_ticker_parts`.

No API key required. Returns a formatted plaintext block ready for prompt
injection and degrades gracefully — returns a placeholder string rather
than raising — so callers never special-case missing data.
"""

from __future__ import annotations

import logging
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tradingagents.dataflows.symbol_utils import china_ticker_parts

logger = logging.getLogger(__name__)

_NEWS_URL = (
    "https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{sym}.phtml"
)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Each entry inside <div class="datelist"><ul>…</ul></div> follows the shape:
#
#   &nbsp;&nbsp;&nbsp;&nbsp;YYYY-MM-DD&nbsp;HH:MM&nbsp;&nbsp;
#   <a target='_blank' href='URL'>headline</a> <br>
#
# i.e. date/time appear *before* the link, separated by HTML &nbsp; entities,
# and attribute values use single quotes. Match accordingly; allow either
# quote style on href so the regex survives a future markup tweak.
_DATELIST_RE = re.compile(
    r'<div[^>]*class="datelist"[^>]*>(?P<body>.*?)</div>', re.DOTALL
)
_ENTRY_RE = re.compile(
    r'(?P<date>\d{4}-\d{2}-\d{2})&nbsp;(?P<time>\d{2}:\d{2})'
    r'(?:&nbsp;|\s)*'
    r'<a[^>]+href=["\'](?P<url>[^"\']+)["\'][^>]*>(?P<title>[^<]+)</a>',
    re.DOTALL,
)


def fetch_sina_finance_news(ticker: str, limit: int = 20, timeout: float = 10.0) -> str:
    """Fetch recent Sina Finance headlines for ``ticker`` and return a
    formatted plaintext block ready for prompt injection.

    Only Chinese A-share tickers (``.SS`` / ``.SZ``) are supported; other
    tickers return an explanatory placeholder so the caller can stay generic.
    """
    parts = china_ticker_parts(ticker)
    if parts is None:
        return (
            f"<Sina Finance only covers Chinese A-shares; "
            f"{ticker} is not an A-share symbol>"
        )

    prefix, code = parts
    sina_symbol = f"{prefix}{code}"
    url = _NEWS_URL.format(sym=sina_symbol)

    req = Request(url, headers={"User-Agent": _UA, "Accept": "text/html"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        logger.warning("Sina Finance fetch failed for %s: %s", ticker, exc)
        return f"<sina finance unavailable: {type(exc).__name__}>"

    # Sina serves GB18030; fall back to a permissive decode so a single
    # malformed byte doesn't lose the whole page.
    try:
        html = raw.decode("gb18030", errors="replace")
    except LookupError:
        html = raw.decode("utf-8", errors="replace")

    block_match = _DATELIST_RE.search(html)
    if not block_match:
        return f"<no Sina Finance news found for {sina_symbol}>"

    entries = list(_ENTRY_RE.finditer(block_match.group("body")))
    if not entries:
        return f"<no Sina Finance news found for {sina_symbol}>"

    lines = []
    for m in entries[:limit]:
        title = m.group("title").strip()
        timestamp = f"{m.group('date')} {m.group('time')}"
        lines.append(f"[{timestamp}] {title}")

    header = (
        f"Sina Finance — {len(lines)} recent headlines for {sina_symbol}:"
    )
    return header + "\n" + "\n".join(lines)
