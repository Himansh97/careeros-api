"""A careers hostname is not a mail domain, and an ATS is not the employer.

Two lookups came back with zero contacts and read as "nobody works there".
Neither was true:

* Datadog's apply URL is `careers.datadoghq.com`. That is genuinely Datadog's
  host, so the third-party check correctly let it through — but nobody has a
  mailbox on it, so the search found nothing. Asked about `datadoghq.com` the
  same lookup returns six people.
* Lyft's posting is hosted on `app.careerpuck.com`, an applicant-tracking
  vendor that was missing from the blocklist. The lookup went off to search an
  ATS for Lyft's recruiters — the same failure already recorded above that list,
  where a Dice-hosted posting produced a Dice recruiter, just with a host nobody
  had hit yet.

Both burn a paid lookup to produce a confident wrong answer, which is worse than
either a right one or an error.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contacts import company_domain  # noqa: E402


def test_a_careers_subdomain_resolves_to_the_mail_domain() -> None:
    assert company_domain("Datadog", "https://careers.datadoghq.com/detail/1/") == "datadoghq.com"
    assert company_domain("Acme", "https://jobs.acme.com/x") == "acme.com"
    assert company_domain("Acme", "https://apply.acme.com/x") == "acme.com"


def test_www_is_stripped_as_before() -> None:
    assert company_domain("Acme", "https://www.acme.com/careers") == "acme.com"


def test_a_multi_label_public_suffix_survives() -> None:
    """Taking the last two labels would turn this into `co.uk`."""
    assert company_domain("Brit", "https://careers.example.co.uk/x") == "example.co.uk"


def test_a_plain_domain_is_left_alone() -> None:
    assert company_domain("Tech Rakers", "https://techrakers.com/careers") == "techrakers.com"


def test_an_ats_host_returns_nothing_rather_than_its_own_staff() -> None:
    assert company_domain("Lyft", "https://app.careerpuck.com/job-board/lyft/1") is None
    assert company_domain("AIT Global", "https://www.dice.com/jobs/detail/9") is None
    assert company_domain("X", "https://boards.greenhouse.io/x/jobs/1") is None


def test_no_url_is_not_a_domain() -> None:
    assert company_domain("Athereon", "") is None


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
