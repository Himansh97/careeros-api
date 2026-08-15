"""The only place that calls Anthropic, so the only place that can spend money.

Every request goes through `complete()`. That is not a style preference — it is
what makes the ledger in `app/usage.py` complete. A second call site that talks
to the SDK directly would be invisible to the budget, and a budget with a hole
in it is worse than none because it reads as a guarantee.

Three refusals, all returning None rather than raising:

* no API key
* the budget for today or this month is spent
* the request failed

Callers treat all three identically: fall back to the deterministic pipeline.
A resume must always be producible without a model, so nothing here may turn
"you have spent enough this month" into "your resume failed to generate".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import ANTHROPIC_MODEL, anthropic_key
from .usage import check_budget, record

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float


def available() -> tuple[bool, str]:
    """Whether a call would be attempted, and why not when it would not."""
    if not anthropic_key():
        return False, "no ANTHROPIC_API_KEY configured"
    reason = check_budget()
    if reason:
        return False, reason
    return True, ""


def complete(
    prompt: str,
    *,
    purpose: str,
    system: str = "",
    job_id: str | None = None,
    max_tokens: int = 2000,
    model: str | None = None,
) -> Completion | None:
    """One request, always metered. None means "use the rule-based path".

    `purpose` is required and lands in the ledger, so spend can be attributed
    to the feature that caused it rather than showing up as an undifferentiated
    monthly number.
    """
    ok, why = available()
    if not ok:
        logger.info("llm: skipping %s — %s", purpose, why)
        return None

    chosen = model or ANTHROPIC_MODEL

    try:
        import anthropic
    except ImportError:
        logger.warning("llm: anthropic SDK not installed; staying rule-based")
        return None

    client = anthropic.Anthropic(api_key=anthropic_key())
    kwargs: dict[str, Any] = {
        "model": chosen,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        # Cached: the containment rules are identical across every job, so
        # after the first call of a run this is read at a tenth of input price.
        kwargs["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]

    try:
        resp = client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - a failed call must not break a resume
        # Recorded with ok=0 and no tokens: a request that never reached the
        # model costs nothing, but the attempt is worth seeing in the ledger.
        record(chosen, purpose, job_id=job_id, ok=False, detail=f"{type(exc).__name__}: {exc}"[:400])
        logger.warning("llm: %s failed — %s", purpose, type(exc).__name__)
        return None

    usage = resp.usage
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

    cost = record(
        chosen,
        purpose,
        job_id=job_id,
        input_tokens=int(usage.input_tokens),
        output_tokens=int(usage.output_tokens),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        ok=True,
    )

    return Completion(
        text=text,
        model=chosen,
        input_tokens=int(usage.input_tokens),
        output_tokens=int(usage.output_tokens),
        cache_read_tokens=cache_read,
        cost_usd=cost,
    )
