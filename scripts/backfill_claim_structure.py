"""Propose metrics, scope and seniority for every claim — dry-run by default.

The five new fields on `EvidenceClaim` exist so a rewrite can be checked
against something. `metrics` binds each figure to what it measured, so moving a
number onto a different noun is detectable. `seniority_verb` records the true
authority level, which is the only defence against a model turning "supported"
into "drove" using words already in the sentence.

Everything here is *derived from the claim text the candidate already wrote*,
never invented. Where the text does not say, the field stays empty — an empty
`seniority_verb` makes the containment check refuse to raise the verb at all,
which is the safe direction. A guessed one would licence exactly the inflation
the field is meant to prevent.

    ./.venv/bin/python scripts/backfill_claim_structure.py            # dry run
    ./.venv/bin/python scripts/backfill_claim_structure.py --write
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import CAREEROS_DIR  # noqa: E402

# The ladder the containment check will compare against. Order is the point.
VERB_TIERS: dict[str, int] = {
    "assist": 0, "help": 0, "contribut": 0, "particip": 0,
    "support": 1, "maintain": 1, "monitor": 1, "track": 1,
    "gather": 2, "analyz": 2, "analys": 2, "document": 2, "test": 2,
    "validat": 2, "process": 2, "clean": 2, "prepar": 2, "compil": 2,
    "appli": 2, "us": 2, "ran": 2, "run": 2, "conduct": 2, "present": 2,
    "mentor": 2, "diagnos": 2,
    "built": 3, "build": 3, "develop": 3, "engineer": 3, "implement": 3,
    "design": 3, "creat": 3, "deploy": 3, "automat": 3, "standardiz": 3,
    "synthesiz": 3, "integrat": 3, "consolidat": 3,
    "led": 4, "lead": 4, "driv": 4, "drove": 4, "coordinat": 4,
    "manag": 4, "spearhead": 4, "orchestrat": 4,
    "own": 5, "direct": 5, "head": 5, "govern": 5, "establish": 5,
}

_NUMBER = re.compile(r"\d[\d,\.]*\+?%?|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)
_STOP = set("the a an and or of to in for with on by that this as is are was were from into using across".split())


def _stem(word: str) -> str:
    w = word.lower()
    for suffix in ("ing", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return w[: -len(suffix)]
    return w


def seniority_of(text: str) -> str:
    """The claim's own opening verb, when it is one we can rank.

    Only the first word. A verb buried mid-sentence usually belongs to someone
    else or to a sub-clause, and reading it as the candidate's authority is the
    mistake the whole field exists to prevent.
    """
    first = re.findall(r"[A-Za-z]+", text)
    if not first:
        return ""
    stem = _stem(first[0])
    return stem if stem in VERB_TIERS else ""


def metrics_of(text: str) -> list[dict[str, str]]:
    """Each figure, bound to the words immediately around it.

    `of` is the nearest content words following the number, which is where the
    thing measured almost always sits in this candidate's phrasing ("by 40%" is
    the exception and is handled by looking backwards too).
    """
    out: list[dict[str, str]] = []
    tokens = re.findall(r"\d[\d,\.]*[KMB]?\+?%?|[A-Za-z][A-Za-z\-]*", text)
    for i, tok in enumerate(tokens):
        if not re.fullmatch(r"\d[\d,\.]*[KMB]?\+?%?", tok):
            continue
        after = [t for t in tokens[i + 1 : i + 5] if t.lower() not in _STOP and len(t) > 3]
        before = [t for t in tokens[max(0, i - 4) : i] if t.lower() not in _STOP and len(t) > 3]
        subject = " ".join(after[:3]) or " ".join(before[-3:])
        bare = tok.rstrip("+")
        unit = (
            "percent" if tok.endswith("%")
            else "count" if (bare.isdigit() or re.fullmatch(r"\d[\d,\.]*[KMB]", bare))
            else ""
        )
        out.append({"value": tok, "unit": unit, "of": subject})
    return out


def scope_of(text: str) -> dict[str, object]:
    """Size, only where the sentence states it."""
    scope: dict[str, object] = {}
    if m := re.search(r"team of (\d+|one|two|three|four|five|six|seven|eight|nine|ten)", text, re.I):
        scope["team_size"] = m.group(1)
    if m := re.search(r"(\d[\d,]*\+?)\s*(?:regional\s+)?markets", text, re.I):
        scope["markets"] = m.group(1)
    if m := re.search(r"(\d[\d,]*[KM]?\+?)\s*records", text, re.I):
        scope["records"] = m.group(1)
    if m := re.search(r"(\d[\d,]*\+?)\s*(?:source\s+)?systems", text, re.I):
        scope["systems"] = m.group(1)
    if m := re.search(r"(\d[\d,]*\+?)\s*accounts", text, re.I):
        scope["accounts"] = m.group(1)
    return scope


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply; otherwise report only")
    args = ap.parse_args()

    path = CAREEROS_DIR / "career_evidence.json"
    data = json.loads(path.read_text())
    claims = data["claims"]

    changed = 0
    no_verb: list[str] = []
    restated: list[tuple[str, str, str]] = []
    for c in claims:
        text = c["claim"]
        proposed = {
            "metrics": metrics_of(text),
            "scope": scope_of(text),
            "seniority_verb": seniority_of(text),
        }
        if not proposed["seniority_verb"]:
            no_verb.append(c["claim_id"])

        # metrics and scope are only filled when absent: the candidate may have
        # corrected them by hand and a re-run must not stamp over that.
        for k in ("metrics", "scope"):
            if proposed[k] and not c.get(k):
                c[k] = proposed[k]

        # seniority_verb is different. It is purely mechanical — the claim's
        # own first word — so it must track the text rather than persist. When
        # a claim is edited from "Mentored" to "Led a team of 4", a stale verb
        # of `mentor` would make the containment check reject the claim's own
        # true wording as inflation. The text is the authority.
        derived = proposed["seniority_verb"]
        if derived and c.get("seniority_verb") != derived:
            if c.get("seniority_verb"):
                restated.append((c["claim_id"], c["seniority_verb"], derived))
            c["seniority_verb"] = derived
            changed += 1

    counts = {
        "with metrics": sum(1 for c in claims if c.get("metrics")),
        "with scope": sum(1 for c in claims if c.get("scope")),
        "with seniority_verb": sum(1 for c in claims if c.get("seniority_verb")),
    }
    print(f"{len(claims)} claims")
    for k, v in counts.items():
        print(f"  {k:22} {v}")
    if restated:
        print(f"\n  seniority re-derived after a claim edit ({len(restated)}):")
        for cid, was, now_ in restated:
            print(f"    {cid:22} {was!r} -> {now_!r}")
    if no_verb:
        print(f"\n  no rankable opening verb ({len(no_verb)}): {', '.join(no_verb[:8])}")
        print("  these stay empty, so a rewrite may not raise their verb at all")

    print("\n  role_family and proves are deliberately NOT derived — they are")
    print("  judgements about the work, not facts extractable from a sentence.")

    if not args.write:
        print("\n  dry run — nothing written. Pass --write to apply.")
        return 0

    backup = path.with_suffix(f".backup-{time.strftime('%Y%m%d-%H%M%S')}.json")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)
    print(f"\n  written; backup at {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
