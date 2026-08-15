"""Is the Anthropic key configured, and does it work?

Run after adding ANTHROPIC_API_KEY to the gitignored .env:

    ./.venv/bin/python scripts/check_anthropic.py

It never prints the key, only whether one is present, its length, and its last
four characters — enough to tell two keys apart in a terminal without putting a
usable secret on screen or into shell history.

A missing key is not an error here. The resume writer is designed to fall back
to the deterministic pipeline, so this reports the state rather than failing.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import ANTHROPIC_MODEL, anthropic_key  # noqa: E402


def main() -> int:
    key = anthropic_key()

    if not key:
        print("  no ANTHROPIC_API_KEY found")
        print("  the resume writer will stay rule-based, which is a supported state")
        print("\n  to set one, without it entering this terminal's history:")
        print("    cd ~/careeros-api && printf 'ANTHROPIC_API_KEY=%s\\n' \"$(read -rs -p 'key: ' k; echo \"$k\")\" >> .env")
        return 0

    print(f"  key present  · {len(key)} chars · ends {key[-4:]}")
    print(f"  model        · {ANTHROPIC_MODEL}")

    try:
        import anthropic
    except ImportError:
        print("  SDK          · NOT INSTALLED — pip install anthropic")
        return 1

    print(f"  SDK          · anthropic {anthropic.__version__}")

    # One minimal call. Cheaper than a token of doubt about whether the key,
    # the model name and the network all work before anything depends on them.
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        print(f"  live call    · ok — model replied {text!r}")
        print(f"  usage        · {resp.usage.input_tokens} in, {resp.usage.output_tokens} out")
        return 0
    except Exception as exc:  # noqa: BLE001 - the point is to report, not raise
        name = type(exc).__name__
        print(f"  live call    · FAILED ({name})")
        # Print the message but not the key, which some SDK errors echo back.
        detail = str(exc)
        if key in detail:
            detail = detail.replace(key, "<redacted>")
        print(f"                 {detail[:300]}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
