"""What the model actually cost, and a limit that actually stops it.

Two jobs, and the second is the one that matters. Recording usage after the
fact is bookkeeping; refusing a call that would breach a budget is a control.
This module does both, and the control is checked *before* every request rather
than reported after it — a spend dashboard that can only tell you that you have
already overspent is a receipt, not a limit.

**Prices are per million tokens and were read from the published table**, not
estimated. Cache reads cost a tenth of base input and cache writes a quarter
more, so a run that reuses a cached system prompt is genuinely cheaper and the
figures here reflect that rather than pretending every input token costs the
same.

**An unknown model is not free.** If a model appears that this table has never
heard of, cost falls back to the most expensive rate known rather than to zero.
Recording an unpriced call as $0.00 would quietly break the budget, and the
failure would look exactly like frugality.

The budget is deliberately conservative about its own accuracy: it refuses on
the *recorded* spend, which lags any call currently in flight. With one request
at a time that is exact; the moment anything runs concurrently it is a small
undercount, which is why the caps are set well below anything that matters.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import connect, now

# USD per million tokens, from the published pricing table.
_M = 1_000_000.0

PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {
        "input": 2.00, "output": 10.00,
        "cache_write_5m": 2.50, "cache_write_1h": 4.00, "cache_read": 0.20,
    },
    "claude-opus-5": {
        "input": 5.00, "output": 25.00,
        "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.00, "output": 5.00,
        "cache_write_5m": 1.25, "cache_write_1h": 2.00, "cache_read": 0.10,
    },
}

# What an unrecognised model is charged at. The dearest known rate, so a model
# this table has not caught up with can never look cheap.
_UNKNOWN = max(PRICES.values(), key=lambda p: p["output"])

# Caps, overridable in the gitignored .env. Defaults chosen against the real
# shape of the work: the autopilot tailors at most 10 resumes a day, and a
# rewrite pass on one resume is a few cents on Sonnet.
DAILY_BUDGET_USD = float(os.environ.get("LLM_DAILY_BUDGET_USD", "2.00"))
MONTHLY_BUDGET_USD = float(os.environ.get("LLM_MONTHLY_BUDGET_USD", "25.00"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  at                  TEXT NOT NULL,
  model               TEXT NOT NULL,
  purpose             TEXT NOT NULL,
  job_id              TEXT,
  input_tokens        INTEGER NOT NULL DEFAULT 0,
  output_tokens       INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
  cost_usd            REAL NOT NULL DEFAULT 0,
  ok                  INTEGER NOT NULL DEFAULT 1,
  detail              TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_at ON llm_usage(at);
"""


def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def price_for(model: str) -> dict[str, float]:
    return PRICES.get(model, _UNKNOWN)


def cost_usd(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Cost of one call, in dollars.

    Cache writes are billed at the 5-minute rate. The 1-hour rate is double
    base input rather than 1.25x, so assuming 5m understates a 1h run — that is
    the safe direction for a *recorded* figure to be wrong in only if the
    budget is also conservative, which is why the caps sit well below the point
    where the difference matters. Revisit if 1h caching is ever used here.
    """
    p = price_for(model)
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read_tokens * p["cache_read"]
        + cache_write_tokens * p["cache_write_5m"]
    ) / _M


def record(
    model: str,
    purpose: str,
    *,
    job_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    ok: bool = True,
    detail: str = "",
) -> float:
    """Write one call to the ledger and return what it cost.

    Failed calls are recorded too, with `ok=0`. A request that errored after
    the tokens were counted still cost money, and a ledger that only shows
    successes will drift from the real bill.
    """
    amount = cost_usd(
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT INTO llm_usage (at, model, purpose, job_id, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, ok, detail)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now(), model, purpose, job_id, input_tokens, output_tokens,
             cache_read_tokens, cache_write_tokens, amount, 1 if ok else 0, detail[:500]),
        )
    return amount


def _spent_since(conn: sqlite3.Connection, iso: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE at >= ?", (iso,)
    ).fetchone()
    return float(row[0] or 0.0)


def _money(amount: float) -> str:
    """Dollars, with enough precision to be worth reading.

    Two decimals is right for a real budget and useless below a cent — a cap of
    $0.00005 rendered "daily budget reached — $0.00 of $0.00 spent today",
    which says nothing. Small amounts get the precision they need.
    """
    if amount and abs(amount) < 0.01:
        return f"${amount:.6f}".rstrip("0")
    return f"${amount:.2f}"


def _window_starts() -> tuple[str, str]:
    n = datetime.now(timezone.utc)
    day = n.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return day.strftime("%Y-%m-%dT%H:%M:%SZ"), month.strftime("%Y-%m-%dT%H:%M:%SZ")


def budget_state() -> dict[str, Any]:
    """Spend against both caps, and whether a call may proceed."""
    day_start, month_start = _window_starts()
    with connect() as conn:
        _ensure(conn)
        today = _spent_since(conn, day_start)
        month = _spent_since(conn, month_start)

    # Comparisons use the unrounded figures; only the returned values are
    # rounded, and only for display.
    blocked = None
    if DAILY_BUDGET_USD > 0 and today >= DAILY_BUDGET_USD:
        blocked = (
            f"daily budget reached — {_money(today)} of {_money(DAILY_BUDGET_USD)} spent today"
        )
    elif MONTHLY_BUDGET_USD > 0 and month >= MONTHLY_BUDGET_USD:
        blocked = (
            f"monthly budget reached — {_money(month)} of {_money(MONTHLY_BUDGET_USD)} this month"
        )

    return {
        "today": round(today, 4),
        "month": round(month, 4),
        "dailyBudget": DAILY_BUDGET_USD,
        "monthlyBudget": MONTHLY_BUDGET_USD,
        "dailyRemaining": round(max(0.0, DAILY_BUDGET_USD - today), 4),
        "monthlyRemaining": round(max(0.0, MONTHLY_BUDGET_USD - month), 4),
        "blocked": blocked is not None,
        "reason": blocked,
    }


def check_budget() -> str | None:
    """None when a call may proceed, otherwise why it may not.

    Callers must treat a reason the same way they treat a missing API key: fall
    back to the deterministic path. A budget that raises would turn "you have
    spent enough this month" into "your resume failed to generate".
    """
    state = budget_state()
    return state["reason"] if state["blocked"] else None


def summary(days: int = 30) -> dict[str, Any]:
    """The ledger, aggregated for display."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with connect() as conn:
        _ensure(conn)
        conn.row_factory = sqlite3.Row

        totals = conn.execute(
            "SELECT COUNT(*) calls, COALESCE(SUM(input_tokens),0) inp,"
            " COALESCE(SUM(output_tokens),0) outp,"
            " COALESCE(SUM(cache_read_tokens),0) cread,"
            " COALESCE(SUM(cache_write_tokens),0) cwrite,"
            " COALESCE(SUM(cost_usd),0) cost,"
            " COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) failed"
            " FROM llm_usage WHERE at >= ?",
            (since,),
        ).fetchone()

        by_day = [
            dict(r)
            for r in conn.execute(
                "SELECT substr(at,1,10) day, COUNT(*) calls,"
                " COALESCE(SUM(cost_usd),0) cost,"
                " COALESCE(SUM(input_tokens+output_tokens),0) tokens"
                " FROM llm_usage WHERE at >= ? GROUP BY day ORDER BY day",
                (since,),
            )
        ]
        by_purpose = [
            dict(r)
            for r in conn.execute(
                "SELECT purpose, COUNT(*) calls, COALESCE(SUM(cost_usd),0) cost"
                " FROM llm_usage WHERE at >= ? GROUP BY purpose ORDER BY cost DESC",
                (since,),
            )
        ]
        by_model = [
            dict(r)
            for r in conn.execute(
                "SELECT model, COUNT(*) calls, COALESCE(SUM(cost_usd),0) cost"
                " FROM llm_usage WHERE at >= ? GROUP BY model ORDER BY cost DESC",
                (since,),
            )
        ]
        recent = [
            dict(r)
            for r in conn.execute(
                "SELECT at, model, purpose, job_id, input_tokens, output_tokens,"
                " cache_read_tokens, cost_usd, ok, detail"
                " FROM llm_usage ORDER BY id DESC LIMIT 25"
            )
        ]

    return {
        "windowDays": days,
        "totals": {
            "calls": totals["calls"],
            "failed": totals["failed"],
            "inputTokens": totals["inp"],
            "outputTokens": totals["outp"],
            "cacheReadTokens": totals["cread"],
            "cacheWriteTokens": totals["cwrite"],
            "costUsd": round(float(totals["cost"]), 4),
        },
        "byDay": by_day,
        "byPurpose": by_purpose,
        "byModel": by_model,
        "recent": recent,
        "budget": budget_state(),
        "prices": PRICES,
        "note": (
            "Cost is computed from the published per-million-token rates at the "
            "time of each call, not read back from Anthropic. It will track your "
            "invoice closely but is not the invoice — check the Console for that."
        ),
    }
