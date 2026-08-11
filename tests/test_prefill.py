"""Regression checks for application-field routing."""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.prefill import match_field  # noqa: E402
from scripts import prefill_apply  # noqa: E402


def main() -> int:
    failures: list[str] = []

    # GitLab says "current location" inside its sponsorship question. The
    # generic address matcher must not claim it before the specific question
    # can route to the candidate's stated future-sponsorship answer.
    gitlab_label = (
        "question_35678887002 Will you now or in the future require "
        "sponsorship for a visa to remain in your current location?*"
    )
    actual = match_field(gitlab_label)
    if actual != "sponsorship_future":
        failures.append(
            "GitLab future-sponsorship field routed to "
            f"{actual!r}, want 'sponsorship_future'"
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        wrong = root / "Gitlab" / "wrong-role.pdf"
        wrong.parent.mkdir()
        wrong.write_bytes(b"wrong")
        right = (
            root
            / "Gitlab"
            / "Senior_Backend_Engineer_Analytics_Instrumentation_Golang"
            / "right-role.pdf"
        )
        right.parent.mkdir()
        right.write_bytes(b"right")

        original_out = prefill_apply.OUT
        prefill_apply.OUT = root
        try:
            selected = prefill_apply.resume_for(
                "gh_gitlab_8451512002",
                "Gitlab",
                "Senior Backend Engineer, Analytics Instrumentation (Golang)",
            )
        finally:
            prefill_apply.OUT = original_out

        if selected != right:
            failures.append(
                f"role-specific résumé selection returned {selected}, want {right}"
            )

    for failure in failures:
        print(f"FAIL {failure}")
    if not failures:
        print("PASS GitLab future-sponsorship field routing")
        print("PASS role-specific résumé selection")
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
