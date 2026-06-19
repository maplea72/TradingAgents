"""In-memory async job store for the TradingAgents API server.

LangGraph's ``propagate()`` is a synchronous call that can take 30-120s.
FastAPI handlers are async, so we run the graph in a thread and track
progress through a small in-memory registry keyed by job UUID.

This is intentionally lightweight — a process-local dict and one
``asyncio.Task`` per job. That's enough for a single Render instance.
If you ever scale to multiple workers or need restart-survival, swap
``_JOBS`` for Redis (the public methods stay the same).
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

logger = logging.getLogger(__name__)

JobStatus = Literal["pending", "running", "done", "error"]


@dataclass
class Job:
    """One ``/analyze`` invocation tracked from submission to completion."""

    id: str
    ticker: str
    trade_date: str
    asset_type: str
    selected_analysts: list[str]
    status: JobStatus = "pending"
    result: dict[str, Any] | None = None
    signal: dict[str, Any] | None = None
    error: str | None = None
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def public_dict(self) -> dict[str, Any]:
        """Shape returned by ``GET /analyze/{job_id}``.

        Excludes any internal fields the teammate's LLM doesn't need.
        """
        return {
            "id": self.id,
            "status": self.status,
            "ticker": self.ticker,
            "trade_date": self.trade_date,
            "asset_type": self.asset_type,
            "selected_analysts": self.selected_analysts,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "signal": self.signal,
            "error": self.error,
        }


_JOBS: dict[str, Job] = {}


def submit_job(
    ticker: str,
    trade_date: str,
    asset_type: str = "stock",
    selected_analysts: list[str] | None = None,
) -> Job:
    """Register a new job and kick off its execution in the background.

    Returns immediately with a ``pending`` job. The caller polls
    :func:`get_job` until ``status`` flips to ``done`` or ``error``.
    """
    analysts = selected_analysts or ["market", "social", "news", "fundamentals"]
    job = Job(
        id=str(uuid.uuid4()),
        ticker=ticker,
        trade_date=trade_date,
        asset_type=asset_type,
        selected_analysts=analysts,
    )
    _JOBS[job.id] = job
    asyncio.create_task(_run_job(job))
    return job


def get_job(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


async def _run_job(job: Job) -> None:
    """Execute the graph in a thread; mutate the job in place as it progresses."""
    job.status = "running"
    job.started_at = time.time()
    try:
        # propagate() is synchronous and CPU/IO-bound; offload to a thread
        # so the FastAPI event loop stays responsive for status polls.
        result, signal = await asyncio.to_thread(_run_graph_sync, job)
        job.result = result
        job.signal = signal
        job.status = "done"
    except Exception as exc:
        logger.exception("Job %s failed", job.id)
        job.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        job.status = "error"
    finally:
        job.finished_at = time.time()


def _run_graph_sync(job: Job) -> tuple[dict[str, Any], dict[str, Any] | str]:
    """Build a graph, run it, and return the structured log + processed signal.

    The structured log mirrors what ``_log_state`` writes to disk — that's the
    canonical "analyst reports + debates + final decision" shape we want in
    the API response.
    """
    graph = TradingAgentsGraph(
        selected_analysts=job.selected_analysts,
        debug=False,
        config=DEFAULT_CONFIG.copy(),
    )
    graph.propagate(job.ticker, job.trade_date, asset_type=job.asset_type)
    structured = graph.log_states_dict[str(job.trade_date)]
    signal = graph.process_signal(structured["final_trade_decision"])
    return structured, signal
