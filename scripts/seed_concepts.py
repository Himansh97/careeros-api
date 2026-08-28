"""Store concept explanations, refusing any source that does not resolve.

Written the way `seed_question_research.py` is: an agent session with web access
looks the material up, writes it here, and this script stores it. A model asked
at runtime for a citation produces a plausible URL that does not exist, which is
worse than having no definition at all — so the research happens once, by hand,
and lands in the database rather than being regenerated per request.

What this adds over `concepts.save_note` on its own: **the URLs are checked.**
`save_note` refuses an empty source list, which catches the lazy case and not
the confident one. A citation that 404s is the failure that actually happens,
and it is invisible until somebody clicks it in an interview week.

Wikipedia is verified through its REST summary API rather than by fetching the
page. A plain GET to a Wikipedia article from a script returns 403 — their bot
filter, not a missing page — so checking the article URL directly reports every
correct citation as broken.

    ./.venv/bin/python scripts/seed_concepts.py [--dry-run]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import pathlib
import sys
import urllib.parse

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.concepts import save_note, save_topic  # noqa: E402
from app.db import initialize  # noqa: E402

# Wikipedia's API policy wants a contact route in the User-Agent and returns 403
# for a generic one — including a browser-like string. A repository URL satisfies
# it and, unlike an email address, is not personal data being handed to a third
# party. Verified: the same request 403s without it and 200s with it.
UA = {"User-Agent": "CareerOS/1.0 (+https://github.com/Himansh97/careeros-api)"}
DERIVED = ["simple", "hindi", "application", "visual"]


def wiki(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))


def _check(url: str) -> tuple[str, bool, str]:
    """Whether a citation actually resolves."""
    try:
        if "en.wikipedia.org/wiki/" in url:
            slug = url.rsplit("/wiki/", 1)[1]
            probe = "https://en.wikipedia.org/api/rest_v1/page/summary/" + slug
            r = httpx.get(probe, timeout=15, follow_redirects=True, headers=UA)
            return url, r.status_code == 200, str(r.status_code)
        r = httpx.head(url, timeout=15, follow_redirects=True, headers=UA)
        if r.status_code >= 400:
            r = httpx.get(url, timeout=15, follow_redirects=True, headers=UA)
        return url, r.status_code < 400, str(r.status_code)
    except Exception as exc:  # noqa: BLE001 - an unreachable source is a bad source
        return url, False, type(exc).__name__


def verify(urls: list[str]) -> dict[str, tuple[bool, str]]:
    with cf.ThreadPoolExecutor(10) as pool:
        return {u: (ok, code) for u, ok, code in pool.map(_check, sorted(set(urls)))}


def load_boards() -> list[dict]:
    from seeds.concept_boards import BOARDS

    return BOARDS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="verify sources and report, write nothing")
    args = ap.parse_args()

    initialize()
    boards = load_boards()
    every_url = [u for b in boards for n in b["notes"] for u in n["sources"]]
    print(f"verifying {len(set(every_url))} distinct sources…")
    checked = verify(every_url)

    broken = {u: code for u, (ok, code) in checked.items() if not ok}
    for url, code in broken.items():
        print(f"  BROKEN {code}  {url}")

    stored = skipped = 0
    for board in boards:
        terms: list[str] = []
        for note in board["notes"]:
            good = [u for u in note["sources"] if checked[u][0]]
            if not good:
                # Never store a definition whose every citation is dead. The
                # sourcing rule is the only thing separating this from a
                # confident guess.
                print(f"  SKIP  {note['term']} — no source resolved")
                skipped += 1
                continue
            terms.append(note["term"])
            if args.dry_run:
                continue
            save_note(
                note["term"], note["definition"], good,
                simple=note["simple"], hindi=note["hindi"],
                application=note["application"], visual=note["visual"],
                derived=DERIVED,
            )
            stored += 1
        # Terms seeded in an earlier pass keep their place on the board.
        terms = terms + [t for t in board.get("also", []) if t not in terms]
        if terms and not args.dry_run:
            save_topic(board["slug"], board["title"], board["blurb"], terms,
                       board.get("order", 0))
            print(f"  board {board['slug']!r}: {len(terms)} concepts")

    verb = "would store" if args.dry_run else "stored"
    print(f"\n{verb} {stored if not args.dry_run else len(every_url) and sum(1 for b in boards for n in b['notes'] if any(checked[u][0] for u in n['sources']))} concepts, "
          f"skipped {skipped}, {len(broken)} broken source(s)")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
