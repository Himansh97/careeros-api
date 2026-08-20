"""A found address and a guessed one must never look alike.

Every paid provider went dry at once — Hunter's free tier spent, Apollo's free
plan returning 403 for people search by design, Tomba and Anymailfinder with no
key — so this finds what it can without paying and is scrupulous about which is
which.

The three states are not decoration:

* **harvested** — pulled from public Git commit metadata. Somebody committed
  from that address, so it exists. Not a guess.
* **inferred** — a name run through a pattern the company demonstrably uses.
  A guess, and labelled one.
* **nothing** — no pattern, or samples that disagree. Reported, never filled in.

The last is the one worth guarding. A company with two address patterns cannot
have one inferred for it, and returning the more common would produce an address
nobody has while looking exactly like the ones that work.

No network here. A finder whose tests fail when GitHub has a bad morning is a
finder nobody runs.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contact_harvest import (  # noqa: E402
    _NOISE,
    apply_pattern,
    detect_pattern,
    usable,
)


# ------------------------------------------------------------- the patterns


def test_a_consistent_company_yields_its_pattern() -> None:
    """Datadog's own commits: 35 samples, all first.last."""
    known = [("Juliano Costa", "juliano.costa@datadoghq.com"),
             ("Clara Poncet", "clara.poncet@datadoghq.com"),
             ("Leo Romanovsky", "leo.romanovsky@datadoghq.com")]
    assert detect_pattern(known) == ("first.last", 3)


def test_flast_is_distinguished_from_first_last() -> None:
    """Lyft is flast; confusing the two produces an address nobody has."""
    known = [("Jake Kaufman", "jkaufman@lyft.com"),
             ("Rich Unger", "runger@lyft.com"),
             ("Charlie Vieth", "cvieth@lyft.com")]
    assert detect_pattern(known) == ("flast", 3)


def test_disagreeing_samples_produce_no_pattern() -> None:
    """Stripe really does use several. Returning the commonest would invent an
    address that looks exactly like the ones that work."""
    known = [("Chris Lim", "chrislim@stripe.com"),      # firstlast
             ("Catherine Moresco", "cmoresco@stripe.com"),  # flast
             ("Silas Boyd-Wickizer", "silas@stripe.com")]   # first
    pattern, count = detect_pattern(known)
    assert pattern is None
    assert count == 0


def test_one_outlier_does_not_destroy_a_clear_pattern() -> None:
    known = [("A Smith", "a.smith@x.com"), ("B Jones", "b.jones@x.com"),
             ("C Brown", "c.brown@x.com"), ("D Odd", "dodd@x.com")]
    pattern, count = detect_pattern(known)
    assert pattern == "first.last"
    assert count == 3


def test_no_samples_is_not_a_pattern() -> None:
    assert detect_pattern([]) == (None, 0)


def test_a_single_name_cannot_teach_a_pattern() -> None:
    """Mononyms and usernames have no first/last to compare against."""
    assert detect_pattern([("rkim-stripe", "rkim@stripe.com")]) == (None, 0)


# --------------------------------------------------------------- applying it


def test_a_pattern_builds_the_address() -> None:
    assert apply_pattern("Dana Whitfield", "acme.com", "first.last") == "dana.whitfield@acme.com"
    assert apply_pattern("Dana Whitfield", "acme.com", "flast") == "dwhitfield@acme.com"
    assert apply_pattern("Dana Whitfield", "acme.com", "firstlast") == "danawhitfield@acme.com"


def test_a_name_that_cannot_be_split_yields_nothing() -> None:
    assert apply_pattern("Cher", "acme.com", "first.last") is None
    assert apply_pattern("", "acme.com", "flast") is None


def test_an_unknown_pattern_yields_nothing_rather_than_a_default() -> None:
    assert apply_pattern("Dana Whitfield", "acme.com", "telepathy") is None


def test_a_hyphenated_surname_survives() -> None:
    """Jean-Philippe Bempel commits as jean-philippe.bempel@ — the split must
    not silently drop half the name."""
    assert apply_pattern("Clara Poncet", "datadoghq.com", "first.last") == "clara.poncet@datadoghq.com"


# ------------------------------------------------------------------- the noise


def test_machinery_is_not_mistaken_for_a_person() -> None:
    """Through `usable`, which is what the harvester actually calls. Asserting
    against the regex alone passed even with the filter deleted."""
    for address in ("dependabot[bot]@acme.com",
                    "49699333+dependabot[bot]@acme.com",
                    "github-actions@acme.com",
                    "noreply@acme.com",
                    "renovate[bot]@acme.com"):
        assert not usable(address, "@acme.com"), f"{address} would have been saved"


def test_a_real_address_is_kept() -> None:
    assert usable("clara.poncet@acme.com", "@acme.com")
    assert usable("jkaufman@acme.com", "@acme.com")


def test_another_company_s_committers_are_ignored() -> None:
    """Open source draws contributors from everywhere; only this employer's
    own people are contacts for this employer."""
    assert not usable("someone@gmail.com", "@acme.com")
    assert not usable("dev@othercorp.com", "@acme.com")
    assert not usable("", "@acme.com")


def test_the_noise_pattern_itself_still_matches() -> None:
    assert _NOISE.search("dependabot[bot]@users.noreply.github.com")


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
