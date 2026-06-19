"""Eastmoney Guba (东方财富股吧) post-list fetcher for Chinese A-share sentiment.

Eastmoney Guba is the largest stock-discussion forum in China — broadly the
Chinese analogue of Reddit + StockTwits combined, organised per ticker. The
public per-symbol list page at ``guba.eastmoney.com/list,<code>.html``
returns a server-rendered HTML table of recent posts with read count, reply
count, title, author, and update date.

Unlike Xueqiu (which is gated behind an Aliyun WAF JS challenge that plain
``urllib`` cannot pass), Eastmoney's Guba list serves the table directly to
any client sending a normal ``User-Agent``, so no headless browser or JS
runtime is needed.

The Chinese-site convention for the list URL is just the bare numeric code
(e.g. ``600519``, ``002594``) regardless of exchange — we accept the
Yahoo-style ``.SS`` / ``.SZ`` suffix for symmetry with the rest of the
project and strip it via :func:`china_ticker_parts`.

No API key required. Returns a formatted plaintext block ready for prompt
injection and degrades gracefully — returns a placeholder string rather
than raising — so callers never special-case missing data.
"""

from __future__ import annotations

import html
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tradingagents.dataflows.symbol_utils import china_ticker_parts

logger = logging.getLogger(__name__)

_LIST_URL = "https://guba.eastmoney.com/list,{code}.html"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# The post table is wrapped in <tbody class="listbody"> ... </tbody>; each
# row carries read count, reply count, title <a>, author <a>, and update
# date in a fixed column order. Pull them per row with a single regex.
_LISTBODY_RE = re.compile(
    r'<tbody[^>]*class="listbody"[^>]*>(?P<body>.*?)</tbody>', re.DOTALL
)
_ROW_RE = re.compile(
    r'<tr[^>]*class="listitem"[^>]*>'
    r'\s*<td><div class="read">(?P<read>[^<]*)</div></td>'
    r'\s*<td><div class="reply">(?P<reply>[^<]*)</div></td>'
    r'\s*<td><div class="title">\s*<a[^>]*>(?P<title>[^<]+)</a>',
    re.DOTALL,
)
_AUTHOR_RE = re.compile(
    r'<div class="author">\s*<a[^>]*>(?P<author>[^<]+)</a>', re.DOTALL
)
_UPDATE_RE = re.compile(
    r'<div class="update">(?P<update>[^<]+)</div>', re.DOTALL
)


def fetch_eastmoney_guba_posts(ticker: str, limit: int = 20, timeout: float = 10.0) -> str:
    """Fetch recent Eastmoney Guba posts for ``ticker`` and return a formatted
    plaintext block ready for prompt injection.

    Only Chinese A-share tickers (``.SS`` / ``.SZ``) are supported; other
    tickers return an explanatory placeholder so the caller can stay generic.
    """
    parts = china_ticker_parts(ticker)
    if parts is None:
        return (
            f"<Eastmoney Guba only covers Chinese A-shares; "
            f"{ticker} is not an A-share symbol>"
        )

    _, code = parts
    url = _LIST_URL.format(code=code)

    req = Request(url, headers={"User-Agent": _UA, "Accept": "text/html"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        logger.warning("Eastmoney Guba fetch failed for %s: %s", ticker, exc)
        return f"<eastmoney guba unavailable: {type(exc).__name__}>"

    # Eastmoney serves UTF-8 for the Guba list page; fall back permissively
    # so a single malformed byte doesn't lose the whole page.
    page = raw.decode("utf-8", errors="replace")

    body_match = _LISTBODY_RE.search(page)
    if not body_match:
        return f"<no Eastmoney Guba posts found for {code}>"

    body = body_match.group("body")
    rows = _split_rows(body)
    if not rows:
        return f"<no Eastmoney Guba posts found for {code}>"

    parsed = []
    for row in rows:
        m = _ROW_RE.search(row)
        if not m:
            continue
        author_m = _AUTHOR_RE.search(row)
        update_m = _UPDATE_RE.search(row)
        parsed.append({
            "read": m.group("read").strip(),
            "reply": m.group("reply").strip(),
            "title": html.unescape(m.group("title").strip()),
            "author": html.unescape(author_m.group("author").strip()) if author_m else "?",
            "update": update_m.group("update").strip() if update_m else "?",
        })
        if len(parsed) >= limit:
            break

    if not parsed:
        return f"<no Eastmoney Guba posts found for {code}>"

    lines = [
        f"[{p['update']} · @{p['author']} · {p['read']:>5}👁 · {p['reply']:>3}c] {p['title']}"
        for p in parsed
    ]
    header = (
        f"Eastmoney Guba (东方财富股吧) — {len(lines)} recent posts for {code} "
        f"(views 👁 / replies c):"
    )
    return header + "\n" + "\n".join(lines)


def _split_rows(body: str) -> list[str]:
    """Split the listbody into per-row HTML chunks.

    Splitting on ``<tr class="listitem">`` is more robust than trying to
    write one regex that consumes a whole row including all its nested tags.
    """
    chunks = re.split(r'<tr[^>]*class="listitem"[^>]*>', body)
    return [f'<tr class="listitem">{c}' for c in chunks[1:]]
