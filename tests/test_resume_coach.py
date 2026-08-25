"""The resume coach may propose. It may not decide.

This is the first feature in the repo where a model writes resume text the
candidate is meant to accept with one click, so the interesting tests are not
"does it work" — they are the ones that fail if the gate is ever routed around.

Four properties, each of which is a way the whole thing quietly becomes a
fabrication machine:

* the verdict shown to the candidate is produced by the SAME function the write
  path uses, so a preview can never say "pass" over something that would be
  written as rejected
* a proposal is stored as `llm`, never as `user` — the author field records who
  vouches for the claim, and laundering generated text into the candidate's own
  tier turns warnings into silence
* a rejected proposal is returned and marked unapplicable rather than hidden;
  the caught fabrication is the point
* a proposal naming a claim the model was never given is dropped, because that
  is a malformed response rather than a containment finding

Plus the honesty rule that applies to every model path here: no key, no budget
and no parse all produce a stated reason and zero proposals. There is no
rule-based fallback, and inventing a reply to fill the silence is the one thing
this module must never do.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import resume_coach  # noqa: E402
from app.overrides import assess_override, verdict_for  # noqa: E402

CLAIM = ("Supported the monthly reporting cycle for 4 analysts, cutting "
         "turnaround by 40% using SQL and Excel.")


class FakeClaim:
    def __init__(self, claim_id: str, claim: str, verb: str = "supported"):
        self.claim_id = claim_id
        self.claim = claim
        self.seniority_verb = verb


class FakeProfile:
    def __init__(self, *claims: FakeClaim):
        self.evidence = claims or (FakeClaim("c1", CLAIM),)


def _resume(*claim_ids: str) -> dict:
    return {
        "sections": [
            {
                "employer": "Acme",
                "bullets": [{"id": cid, "text": CLAIM, "hits": ["SQL"]}
                            for cid in (claim_ids or ("c1",))],
            }
        ]
    }


class FakeCompletion:
    def __init__(self, payload):
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.model = "test"
        self.input_tokens = 1
        self.output_tokens = 1
        self.cache_read_tokens = 0
        self.cost_usd = 0.0001


def _run(payload, profile=None, resume=None, instruction="tighten this"):
    with patch.object(resume_coach, "__name__", resume_coach.__name__):
        with patch("app.llm.available", return_value=(True, "")), \
             patch("app.llm.complete", return_value=FakeCompletion(payload)):
            return resume_coach.coach(
                resume or _resume(), profile or FakeProfile(), instruction
            )


class GateTests(unittest.TestCase):
    def test_the_preview_verdict_comes_from_the_write_path_gate(self) -> None:
        """If these ever diverge, the candidate is shown one answer and given
        another. assess_override is what save_override calls."""
        invented = "Led reporting for 40 analysts using Snowflake and Looker."
        out = _run({"reply": "ok", "proposals": [
            {"claimId": "c1", "text": invented, "why": "stronger"}]})

        direct = assess_override(invented, CLAIM, author="llm",
                                 seniority_ceiling="supported")
        self.assertEqual(out["proposals"][0]["verdict"], direct["verdict"])
        self.assertEqual(out["proposals"][0]["outcome"], direct["outcome"])

    def test_an_invented_figure_is_caught_and_shown(self) -> None:
        out = _run({"reply": "here", "proposals": [
            {"claimId": "c1",
             "text": "Supported reporting for 400 analysts, cutting turnaround by 40%.",
             "why": "bigger"}]})
        proposal = out["proposals"][0]
        self.assertEqual(proposal["verdict"], "reject")
        self.assertFalse(proposal["applicable"])
        self.assertEqual(out["blocked"], 1)
        # And it says WHAT it invented, not merely that something was wrong.
        self.assertTrue(any("400" in f["detail"] for f in proposal["findings"]))

    def test_a_rejected_proposal_is_returned_not_hidden(self) -> None:
        """The caught fabrication is the most informative output here. Dropping
        it would leave the candidate believing the model had no ideas."""
        out = _run({"reply": "", "proposals": [
            {"claimId": "c1",
             "text": "Owned the enterprise data platform across 12 markets.",
             "why": "senior"}]})
        self.assertEqual(len(out["proposals"]), 1)
        self.assertFalse(out["proposals"][0]["applicable"])

    def test_a_contained_rewrite_passes_and_is_applicable(self) -> None:
        out = _run({"reply": "done", "proposals": [
            {"claimId": "c1",
             "text": "Supported monthly reporting for 4 analysts, cutting "
                     "turnaround 40% with SQL.",
             "why": "tighter, same scope"}]})
        proposal = out["proposals"][0]
        self.assertEqual(proposal["verdict"], "pass")
        self.assertTrue(proposal["applicable"])
        self.assertFalse(proposal["queued"])
        self.assertEqual(out["blocked"], 0)

    def test_a_borderline_rewrite_is_queued_rather_than_applied(self) -> None:
        """The llm tier exists for exactly this: worth a human look, not worth a
        silent discard and not worth an automatic write.

        Re-leading on "Cut" is the real case, and it is not a fabrication — the
        claim does say turnaround was cut. It is an unranked verb, so the gate
        cannot confirm the rewrite stays under the recorded ceiling, and
        "cannot confirm" is what the review tier is for. Under `user` this same
        text would apply outright; under `system` it would be refused.
        """
        text = "Cut monthly reporting turnaround 40% for 4 analysts using SQL."
        out = _run({"reply": "", "proposals": [
            {"claimId": "c1", "text": text, "why": "leads with the result"}]})
        proposal = out["proposals"][0]
        self.assertEqual(proposal["verdict"], "review")
        self.assertEqual(proposal["outcome"], "pending_review")
        self.assertTrue(proposal["queued"])
        self.assertTrue(proposal["applicable"])

        # Same sentence, three authors, three outcomes. That asymmetry is the
        # whole policy and it must not collapse.
        self.assertEqual(
            assess_override(text, CLAIM, "user", "supported")["outcome"], "active")
        self.assertEqual(
            assess_override(text, CLAIM, "system", "supported")["outcome"], "rejected")

    def test_three_doubts_escalate_to_a_refusal(self) -> None:
        findings = [type("F", (), {"tier": "review", "detail": "x"})() for _ in range(3)]
        self.assertEqual(verdict_for(findings), "reject")


class ResponseHandlingTests(unittest.TestCase):
    def test_a_proposal_for_an_unknown_claim_is_dropped(self) -> None:
        """Not a containment finding — a malformed response. Reporting it as a
        rejected rewrite would blame the gate for the model's bookkeeping."""
        out = _run({"reply": "ok", "proposals": [
            {"claimId": "does-not-exist", "text": "Anything.", "why": ""}]})
        self.assertEqual(out["proposals"], [])
        self.assertTrue(out["ok"])

    def test_a_fenced_json_response_is_read(self) -> None:
        payload = json.dumps({"reply": "hi", "proposals": []})
        out = _run(f"Sure!\n```json\n{payload}\n```")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reply"], "hi")

    def test_an_unparseable_response_proposes_nothing_and_says_so(self) -> None:
        out = _run("I'm afraid I can't do that.")
        self.assertFalse(out["ok"])
        self.assertEqual(out["proposals"], [])
        self.assertEqual(out["reply"], "")
        self.assertTrue(out["reason"])

    def test_the_number_of_proposals_is_capped(self) -> None:
        many = [{"claimId": "c1", "text": f"Cut turnaround 40% for 4 analysts ({i}).",
                 "why": ""} for i in range(20)]
        out = _run({"reply": "", "proposals": many})
        self.assertLessEqual(len(out["proposals"]), resume_coach.MAX_PROPOSALS)


class HonestFailureTests(unittest.TestCase):
    def test_no_key_or_no_budget_states_the_reason(self) -> None:
        with patch("app.llm.available", return_value=(False, "budget spent")):
            out = resume_coach.coach(_resume(), FakeProfile(), "tighten this")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "budget spent")
        self.assertEqual(out["reply"], "")
        self.assertEqual(out["proposals"], [])

    def test_a_failed_request_does_not_invent_a_reply(self) -> None:
        with patch("app.llm.available", return_value=(True, "")), \
             patch("app.llm.complete", return_value=None):
            out = resume_coach.coach(_resume(), FakeProfile(), "tighten this")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reply"], "")

    def test_an_empty_instruction_costs_nothing(self) -> None:
        with patch("app.llm.complete") as spend:
            out = resume_coach.coach(_resume(), FakeProfile(), "   ")
        spend.assert_not_called()
        self.assertFalse(out["ok"])


class ContextTests(unittest.TestCase):
    def test_the_source_claim_travels_with_every_bullet(self) -> None:
        """Sending bullets alone asks the model to respect a boundary it cannot
        see, then blames it for the misses."""
        ctx = resume_coach.context(_resume(), FakeProfile())
        self.assertEqual(len(ctx["bullets"]), 1)
        self.assertEqual(ctx["bullets"][0]["sourceClaim"], CLAIM)
        self.assertEqual(ctx["bullets"][0]["seniorityCeiling"], "supported")

    def test_a_bullet_with_no_claim_behind_it_is_not_offered(self) -> None:
        """It cannot be contained, so it is not rewritten unchecked."""
        ctx = resume_coach.context(_resume("c1", "orphan"), FakeProfile())
        self.assertEqual([b["claimId"] for b in ctx["bullets"]], ["c1"])

    def test_gaps_are_named_so_the_model_can_decline(self) -> None:
        ctx = resume_coach.context(
            _resume(), FakeProfile(), {"gaps": ["Kubernetes"], "strongMatches": ["SQL"]}
        )
        prompt = resume_coach._prompt(ctx, "add kubernetes", [])
        self.assertIn("Kubernetes", prompt)
        self.assertIn("never write toward these", prompt)

    def test_a_resume_with_nothing_traceable_refuses_before_spending(self) -> None:
        with patch("app.llm.complete") as spend:
            out = resume_coach.coach(_resume("orphan"), FakeProfile(), "fix it")
        spend.assert_not_called()
        self.assertFalse(out["ok"])
        self.assertIn("evidence claim", out["reason"])


class AuthorshipTests(unittest.TestCase):
    def test_the_apply_endpoint_stores_llm_not_user(self) -> None:
        """The author field records who vouches for the claim. Writing generated
        text as `user` turns the warning path into silence."""
        import inspect

        from app import main

        body = inspect.getsource(main.apply_coach_proposal)
        self.assertIn('author="llm"', body)
        self.assertNotIn('author="user"', body)

    def test_the_llm_tier_is_stricter_than_the_candidate(self) -> None:
        """A model may not get the benefit of the doubt that exists for someone
        editing their own history."""
        from app.overrides import _POLICY

        self.assertEqual(_POLICY["llm"], ("active", "pending_review", "rejected"))
        self.assertEqual(_POLICY["user"], ("active", "active", "active"))
        self.assertNotEqual(_POLICY["llm"], _POLICY["user"])

    def test_the_coach_never_assesses_as_the_candidate(self) -> None:
        source = inspect_coach_source()
        self.assertIn('author="llm"', source)
        self.assertNotIn('author="user"', source)


def inspect_coach_source() -> str:
    import inspect

    return inspect.getsource(resume_coach)


if __name__ == "__main__":
    unittest.main(verbosity=1)
