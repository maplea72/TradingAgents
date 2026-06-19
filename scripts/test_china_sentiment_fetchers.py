"""Manual smoke test for the Chinese A-share sentiment fetchers.

Run directly: ``python scripts/test_china_sentiment_fetchers.py``

Exercises ``fetch_xueqiu_posts`` and ``fetch_sina_finance_news`` against a
small set of real A-share tickers and prints what each returns. Designed
to surface upstream failures (HTTP 403, anti-bot pages, encoding drift,
HTML structure changes) clearly so they can be diagnosed without rerunning
the whole sentiment analyst pipeline.

Exit code is 0 when every (ticker, fetcher) pair returned a non-placeholder
block, 1 when any pair degraded to its ``<...unavailable...>`` /
``<no ... found>`` placeholder.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Make the project importable when running the file directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.dataflows.eastmoney_guba import fetch_eastmoney_guba_posts
from tradingagents.dataflows.sina_finance import fetch_sina_finance_news

# Liquid, news-heavy A-shares so a healthy fetcher should always return data.
TICKERS = [
    ("600519.SS", "Kweichow Moutai (贵州茅台)"),
    ("002594.SZ", "BYD (比亚迪)"),
    ("000001.SZ", "Ping An Bank (平安银行)"),
]

# Any block that starts with "<" is one of our placeholder strings.
def _is_placeholder(block: str) -> bool:
    return block.lstrip().startswith("<")


def _summarize(label: str, ticker: str, block: str) -> bool:
    ok = not _is_placeholder(block)
    status = "OK" if ok else "FAIL"
    print(f"\n=== {label} · {ticker} · {status} ===")
    preview = block if len(block) <= 1200 else block[:1200] + "\n…(truncated)"
    print(preview)
    return ok


def main() -> int:
    # Surface fetcher-side warnings (HTTPError, JSONDecodeError, etc.) so a
    # placeholder return is paired with a clear reason on stderr.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    all_ok = True
    for ticker, name in TICKERS:
        print(f"\n########## {ticker} — {name} ##########")
        guba = fetch_eastmoney_guba_posts(ticker, limit=5)
        sina = fetch_sina_finance_news(ticker, limit=5)
        all_ok &= _summarize("Eastmoney Guba", ticker, guba)
        all_ok &= _summarize("Sina Finance", ticker, sina)

    print("\n##########")
    print("Result:", "ALL FETCHERS OK" if all_ok else "ONE OR MORE FETCHERS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
