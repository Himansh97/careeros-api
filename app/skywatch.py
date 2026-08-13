"""Live sky data from public sources, cached per-source and honest about gaps.

Three feeds, none of which needs an API key or an account:

* **JPL Close-Approach Data** — asteroids passing near Earth in the next week.
* **NOAA Space Weather Prediction Center** — the planetary K-index, which is
  the standard 0-9 measure of geomagnetic disturbance.
* **wheretheiss.at** — the ISS's current position, altitude and speed.

Proxied rather than fetched from the browser for the usual reasons: one cache
serves every page load instead of every visitor hammering NASA, and CORS stops
being something three third parties get to decide for us.

**A failed feed is reported, never faked.** Each one carries its own TTL and
its own error state, so a NOAA outage removes the geomagnetic reading and says
so rather than holding the last value forever and presenting it as current. The
whole point of putting live data on the page is that it is live; a stale number
with a fresh timestamp would be worse than no number, which is the same rule
the rest of this system runs on.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Per-source TTLs, set by how fast the underlying thing actually changes.
# The ISS crosses a continent in minutes; the close-approach table for the next
# seven days is effectively static within an hour.
_TTL = {"iss": 5.0, "geomagnetic": 600.0, "approaches": 3600.0}

_cache: dict[str, tuple[float, Any]] = {}

_AU_KM = 149_597_870.7
_EARTH_RADIUS_KM = 6371.0
_TIMEOUT = 12.0


def _fresh(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL[key]:
        return hit[1]
    return None


async def _approaches(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Asteroids passing within 0.05 AU over the next seven days."""
    r = await client.get(
        "https://ssd-api.jpl.nasa.gov/cad.api",
        params={"dist-max": "0.05", "date-min": "now", "date-max": "+7", "sort": "date"},
    )
    r.raise_for_status()
    payload = r.json()
    fields = payload.get("fields", [])
    rows = payload.get("data") or []

    out: list[dict[str, Any]] = []
    for row in rows[:6]:
        rec = dict(zip(fields, row))
        au = float(rec["dist"])
        km = au * _AU_KM
        out.append(
            {
                "designation": rec.get("des"),
                "date": rec.get("cd"),
                "distanceKm": round(km),
                "distanceAu": round(au, 5),
                # Lunar distances are the unit people can actually picture.
                "lunarDistances": round(km / 384_400, 1),
                "velocityKmS": round(float(rec["v_rel"]), 2),
                # Absolute magnitude, not a diameter — JPL does not publish a
                # measured size for most of these and estimating one from H
                # requires an albedo nobody has measured either.
                "magnitudeH": float(rec["h"]) if rec.get("h") else None,
            }
        )
    return out


async def _geomagnetic(client: httpx.AsyncClient) -> dict[str, Any]:
    """The most recent planetary K-index reading from NOAA SWPC."""
    r = await client.get(
        "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    )
    r.raise_for_status()
    rows = r.json()
    latest = rows[-1]
    kp = float(latest["Kp"])
    # NOAA's own G-scale thresholds. Below 5 is not a storm, and saying so
    # matters more than making the number sound exciting.
    if kp < 5:
        level = "quiet" if kp < 4 else "unsettled"
    elif kp < 6:
        level = "G1 minor storm"
    elif kp < 7:
        level = "G2 moderate storm"
    elif kp < 8:
        level = "G3 strong storm"
    else:
        level = "G4+ severe storm"
    return {"kp": kp, "level": level, "observedAt": latest["time_tag"]}


async def _iss(client: httpx.AsyncClient) -> dict[str, Any]:
    r = await client.get("https://api.wheretheiss.at/v1/satellites/25544")
    r.raise_for_status()
    d = r.json()
    return {
        "latitude": round(d["latitude"], 2),
        "longitude": round(d["longitude"], 2),
        "altitudeKm": round(d["altitude"]),
        "velocityKmH": round(d["velocity"]),
        "daylight": d.get("visibility") == "daylight",
    }


async def skywatch() -> dict[str, Any]:
    """Every feed, each cached separately, with failures named."""
    result: dict[str, Any] = {}
    failures: list[str] = []

    wanted: dict[str, Any] = {}
    for key in ("approaches", "geomagnetic", "iss"):
        cached = _fresh(key)
        if cached is not None:
            result[key] = cached
        else:
            wanted[key] = None

    if wanted:
        fetchers = {"approaches": _approaches, "geomagnetic": _geomagnetic, "iss": _iss}
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers={"User-Agent": "CareerOS/1.0"}
        ) as client:
            done = await asyncio.gather(
                *(fetchers[k](client) for k in wanted), return_exceptions=True
            )
        for key, value in zip(wanted, done):
            if isinstance(value, Exception):
                # Named, not swallowed. The UI prints which feed is missing.
                logger.warning("skywatch: %s failed: %s", key, value)
                failures.append(key)
                continue
            _cache[key] = (time.time(), value)
            result[key] = value

    result["failures"] = failures
    result["readAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result["sources"] = {
        "approaches": "NASA/JPL Close-Approach Data API",
        "geomagnetic": "NOAA Space Weather Prediction Center",
        "iss": "wheretheiss.at (NORAD 25544)",
    }
    return result


def earth_radii(km: float) -> float:
    """Distance in Earth radii — the unit close-approach papers actually use."""
    return round(km / _EARTH_RADIUS_KM, 1)
