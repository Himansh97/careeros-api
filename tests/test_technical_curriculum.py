from __future__ import annotations

import unittest

from app.technical_learning.curriculum import (
    CURRICULUM_VERSION,
    get_drill,
    load_curriculum,
    public_curriculum,
)


class TechnicalCurriculumTests(unittest.TestCase):
    def test_curriculum_has_unique_valid_graph(self) -> None:
        curriculum = load_curriculum()
        ids = [drill.id for drill in curriculum.drills]
        self.assertEqual(len(ids), len(set(ids)))
        known = set(ids)
        for drill in curriculum.drills:
            self.assertTrue(set(drill.prerequisites) <= known)
            self.assertTrue(drill.prompt.strip())
            self.assertIn(drill.kind, {"sql", "python", "case"})

    def test_every_skill_track_has_guided_transfer_and_interview_content(self) -> None:
        curriculum = load_curriculum()
        required = {
            "sql", "statistics", "metrics", "data-interpretation", "python",
            "data-modeling", "etl-quality", "dashboard-design",
        }
        for track in required:
            drills = [d for d in curriculum.drills if d.skill == track]
            self.assertTrue(any(d.stage == "practice" for d in drills), track)
            self.assertTrue(any(d.stage == "transfer" for d in drills), track)
            self.assertTrue(any(d.interview_eligible for d in drills), track)

    def test_all_role_missions_are_present(self) -> None:
        roles = {mission.role for mission in load_curriculum().missions}
        self.assertEqual(
            roles,
            {
                "data-analyst",
                "business-analyst",
                "product-analyst",
                "revenue-financial-analyst",
                "analytics-engineer",
            },
        )

    def test_public_curriculum_never_exposes_grading_material(self) -> None:
        public = public_curriculum()
        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value)) if value else set()
            return set()

        public_keys = keys(public)
        for secret in ("expected_sql", "expected_output", "rubric", "solution"):
            self.assertNotIn(secret, public_keys)
        self.assertEqual(public["version"], CURRICULUM_VERSION)

    def test_unknown_version_and_drill_fail_loudly(self) -> None:
        with self.assertRaisesRegex(KeyError, "curriculum version"):
            load_curriculum("1900.01.1")
        with self.assertRaisesRegex(KeyError, "drill"):
            get_drill("missing", CURRICULUM_VERSION)

    def test_python_drills_include_small_public_synthetic_fixtures(self) -> None:
        for drill in (item for item in load_curriculum().drills if item.kind == "python"):
            self.assertTrue(drill.fixture, drill.id)
            self.assertLess(len(repr(drill.fixture)), 10_000)
            self.assertNotIn("expected_output", repr(public_curriculum()["drills"]))


if __name__ == "__main__":
    unittest.main()
