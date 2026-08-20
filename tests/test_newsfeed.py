"""The ticker must not lie about what it could not fetch.

A strip of headlines is trusted precisely because it is unglamorous — it either
shows what a source published or it says the source is missing. Two ways that
breaks, both of which this pins:

* A feed that fails silently produces a *shorter* list, which is indistinguishable
  from a quiet news day. `skywatch` established the rule this follows: a failed
  feed is named in `failures`, never faked, and never served stale.
* A cached feed served past its TTL is a stale headline with a fresh `readAt`,
  which is worse than no headline at all.

Everything here runs against fakes. Hitting Hacker News from a test suite makes
the suite fail when their API has a bad morning, which teaches you to ignore it.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import newsfeed  # noqa: E402


def _reset() -> None:
    newsfeed._cache.clear()


def _item(**over):
    base = {
        "id": "hn-1", "title": "A thing happened", "url": "https://example.test",
        "source": "Hacker News", "topic": "AI", "at": "2026-08-20T00:00:00Z",
        "meta": "100 points",
    }
    base.update(over)
    return base


def _run(**fetchers):
    """Run newsfeed() with the named feeds replaced by fakes."""
    original = dict(newsfeed._FETCHERS)
    newsfeed._FETCHERS.update(fetchers)
    try:
        return asyncio.run(newsfeed.newsfeed())
    finally:
        newsfeed._FETCHERS.clear()
        newsfeed._FETCHERS.update(original)


async def _ok(_client):
    return [_item()]


async def _boom(_client):
    raise RuntimeError("the feed is down")


# ------------------------------------------------------------------- honesty


def test_a_failing_feed_is_named_not_swallowed() -> None:
    _reset()
    out = _run(hackernews=_ok, arxiv=_boom, github=_ok)
    assert "arxiv" in out["failures"], "a dead feed vanished instead of being reported"
    assert len(out["items"]) == 2, "the surviving feeds should still be served"


def test_one_dead_feed_does_not_take_the_others_with_it() -> None:
    _reset()
    out = _run(hackernews=_boom, arxiv=_boom, github=_ok)
    assert out["failures"] == ["hackernews", "arxiv"] or set(out["failures"]) == {
        "hackernews", "arxiv"
    }
    assert len(out["items"]) == 1


def test_a_failed_feed_is_not_cached() -> None:
    """Caching a failure would mean the next caller is told nothing is wrong."""
    _reset()
    _run(hackernews=_ok, arxiv=_boom, github=_ok)
    assert "arxiv" not in newsfeed._cache, "a failure was written to the cache"


def test_every_call_carries_a_read_time_and_its_sources() -> None:
    _reset()
    out = _run(hackernews=_ok, arxiv=_ok, github=_ok)
    assert out["readAt"].endswith("Z")
    assert set(out["sources"]) == {"hackernews", "arxiv", "github"}


# --------------------------------------------------------------------- cache


def test_a_fresh_feed_is_not_refetched() -> None:
    _reset()
    calls = {"n": 0}

    async def counting(_client):
        calls["n"] += 1
        return [_item()]

    _run(hackernews=counting, arxiv=_ok, github=_ok)
    _run(hackernews=counting, arxiv=_ok, github=_ok)
    assert calls["n"] == 1, f"hackernews was fetched {calls['n']} times inside its TTL"


def test_an_expired_feed_is_refetched_rather_than_served_stale() -> None:
    _reset()
    calls = {"n": 0}

    async def counting(_client):
        calls["n"] += 1
        return [_item()]

    _run(hackernews=counting, arxiv=_ok, github=_ok)
    # Backdate past the TTL rather than sleeping for ten minutes.
    stamp, value = newsfeed._cache["hackernews"]
    newsfeed._cache["hackernews"] = (stamp - newsfeed._TTL["hackernews"] - 1, value)
    _run(hackernews=counting, arxiv=_ok, github=_ok)
    assert calls["n"] == 2, "an expired feed was served from cache"


def test_each_source_keeps_its_own_ttl() -> None:
    """A single shared TTL would refetch arXiv every ten minutes for a feed that
    publishes once a day, or hold Hacker News for six hours."""
    assert newsfeed._TTL["hackernews"] < newsfeed._TTL["github"] < newsfeed._TTL["arxiv"]
    assert set(newsfeed._TTL) == set(newsfeed._FETCHERS)


# --------------------------------------------------------------------- shape


def test_every_source_returns_the_same_item_shape() -> None:
    """The strip renders one component for all three feeds; a source that omits
    a key renders a hole."""
    _reset()

    async def gh(_client):
        return [_item(id="gh-1", source="GitHub", meta="900 stars")]

    async def ax(_client):
        return [_item(id="arxiv-1", source="arXiv", meta="cs.LG")]

    out = _run(hackernews=_ok, arxiv=ax, github=gh)
    shapes = {tuple(sorted(i)) for i in out["items"]}
    assert len(shapes) == 1, f"sources disagree on item shape: {shapes}"
    assert set(shapes.pop()) == {"at", "id", "meta", "source", "title", "topic", "url"}


def test_items_are_newest_first() -> None:
    _reset()

    async def older(_client):
        return [_item(id="old", at="2026-08-01T00:00:00Z")]

    async def newer(_client):
        return [_item(id="new", at="2026-08-19T00:00:00Z")]

    async def empty(_client):
        return []

    out = _run(hackernews=older, arxiv=newer, github=empty)
    assert out["items"][0]["id"] == "new", "the feed was not sorted newest first"


# --------------------------------------------------------------------- topics


def test_topic_matching_does_not_fire_inside_a_word() -> None:
    """Plain substring matching tagged "for certain source code" as AI, because
    "cert-ai-n" contains "ai". Every short needle has this problem."""
    assert newsfeed._topic("Google replaced Git tags for certain source code") != "AI"
    assert newsfeed._topic("Chair of the committee resigns") != "AI"


def test_topic_matching_still_catches_the_real_thing() -> None:
    assert newsfeed._topic("Anthropic ships Claude Opus 5") == "AI"
    assert newsfeed._topic("An AI model for weather") == "AI"
    assert newsfeed._topic("DuckDB 1.2 speeds up joins") == "Data"
    assert newsfeed._topic("Stripe launches new payment rails") == "Fintech"
    assert newsfeed._topic("How Kubernetes probes work") == "Infra"


def test_a_prefix_needle_still_matches_its_longer_forms() -> None:
    """Word-boundary anchoring must not break "regulat" -> "regulatory"."""
    assert newsfeed._topic("New regulatory guidance for lenders") == "Fintech"


def test_an_unmatched_headline_falls_back_rather_than_guessing() -> None:
    assert newsfeed._topic("A history of the bicycle") == "Tech"
    assert newsfeed._topic("") == "Tech"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
