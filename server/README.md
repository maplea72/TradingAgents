# TradingAgents API

HTTP wrapper around the TradingAgents LangGraph flow. Lets a remote LLM
drive analyses through tool calls without needing the source repo or
LLM/data API keys on its side.

## Endpoints

| Method | Path                  | Auth         | Purpose                                  |
| ------ | --------------------- | ------------ | ---------------------------------------- |
| GET    | `/healthz`            | none         | Liveness probe (Render uses this)        |
| GET    | `/openapi.json`       | none         | Auto-generated spec — share this with the LLM |
| POST   | `/analyze`            | `X-API-Key`  | Submit a job, returns `{job_id, status}` |
| GET    | `/analyze/{job_id}`   | `X-API-Key`  | Poll status; result populated when `status="done"` |

`POST /analyze` body:

```json
{
  "ticker": "002594.SZ",
  "trade_date": "2026-06-19",
  "asset_type": "stock",
  "selected_analysts": ["market", "social", "news", "fundamentals"]
}
```

`asset_type` and `selected_analysts` are optional. Defaults are `"stock"`
and all four analysts.

## Run locally

```bash
pip install -e .
export TRADINGAGENTS_API_KEY=$(openssl rand -hex 32)
export OPENAI_API_KEY=...        # plus whatever LLM/data keys your config needs
export FINNHUB_API_KEY=...
uvicorn server.main:app --reload --port 8000
```

Smoke test:

```bash
curl http://127.0.0.1:8000/healthz
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $TRADINGAGENTS_API_KEY" \
  -d '{"ticker":"AAPL","trade_date":"2026-06-19"}'
```

## Deploy to Render

The repo ships a `render.yaml` and `Procfile`, so deployment is hands-off:

1. Push the branch.
2. Render dashboard → **New** → **Blueprint** → connect this repo.
3. Render reads `render.yaml`, provisions a `tradingagents-api` web service.
4. Set the secret env vars in the dashboard (they're declared with
   `sync: false` so Render won't read them from the repo):
   - `TRADINGAGENTS_API_KEY` — generate with `openssl rand -hex 32`
   - `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — whichever LLM your
     `default_config` points at
   - `FINNHUB_API_KEY`, `GOOGLE_API_KEY` — for the data flows that need them
5. Deploy. You get back `https://tradingagents-api.onrender.com` (or
   whatever name you picked). Confirm with `curl <url>/healthz`.

The starter plan sleeps after 15 minutes idle and cold-starts on the
first request; that's usually fine because `/analyze` is async and the
LLM polls for the result anyway. Bump to a paid plan if cold starts hurt.

## How the teammate's LLM consumes it

They have three options, in increasing order of polish:

**1. Drop the OpenAPI spec into their tool-calling config.**
Most LLM frameworks accept an OpenAPI URL as a tool source — LangChain
`OpenAPIToolkit`, OpenAI Assistants `tools=[{"type":"function",...}]`
generated from the spec, etc. Point them at
`https://<service>.onrender.com/openapi.json` and have the framework
add `X-API-Key: <shared-secret>` as a default header.

**2. Hand-write two tool functions.** If they prefer explicit control:

```python
def submit_analysis(ticker: str, trade_date: str) -> dict:
    """Start a TradingAgents analysis. Returns {job_id, status}."""
    return requests.post(
        "https://<service>.onrender.com/analyze",
        json={"ticker": ticker, "trade_date": trade_date},
        headers={"X-API-Key": API_KEY},
    ).json()

def get_analysis(job_id: str) -> dict:
    """Poll an analysis. Returns full result once status='done'."""
    return requests.get(
        f"https://<service>.onrender.com/analyze/{job_id}",
        headers={"X-API-Key": API_KEY},
    ).json()
```

**3. MCP bridge** (if they're on Claude Desktop / Claude Code). Wrap the
two HTTP calls in a tiny MCP server. Out of scope for this README, but
the HTTP API is the right primitive to bridge.

## Result shape

When `status="done"`, the `result` field contains:

```jsonc
{
  "company_of_interest": "002594.SZ",
  "trade_date": "2026-06-19",
  "market_report": "...",         // technical analysis
  "sentiment_report": "...",      // social / community sentiment
  "news_report": "...",           // news synthesis
  "fundamentals_report": "...",   // financials synthesis
  "investment_debate_state": { "bull_history": "...", "bear_history": "...", ... },
  "trader_investment_decision": "...",
  "risk_debate_state": { "aggressive_history": "...", ... },
  "investment_plan": "...",
  "final_trade_decision": "..."   // BUY / SELL / HOLD with reasoning
}
```

Plus a top-level `signal` field with the parsed action extracted from
`final_trade_decision`.

## Operational notes

- **Job storage is in-memory.** Restart the service and pending jobs
  are lost. Fine for a single instance and short-lived work; swap
  `_JOBS` in `server/jobs.py` for Redis if you need multi-worker or
  restart-survival.
- **Render request timeout is ~100s by default**, but `/analyze`
  returns immediately with a `job_id`, so a slow graph run won't trip
  it. The status endpoint also returns instantly.
- **The shared API key is the only thing between the public internet
  and your LLM credits.** Rotate it (`openssl rand -hex 32`, update
  the env var, redeploy) if you suspect leakage.
