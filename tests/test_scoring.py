"""Independent arithmetic examples and validation checks for original Game Score."""

from copy import deepcopy
import unittest

from nba_mcp.scoring import (
    FORMULA_SOURCE, FORMULA_VERSION, REQUIRED_FIELDS, ScoringValidationError,
    calculate_game_score, evaluate_game_performance,
)


def example():
    """Return a synthetic 30-point game, not an actual player's performance."""
    return dict(points=30, field_goals_made=10, field_goals_attempted=20,
                free_throws_made=8, free_throws_attempted=10, offensive_rebounds=2,
                defensive_rebounds=6, steals=2, assists=7, blocks=1,
                personal_fouls=3, turnovers=4, plus_minus=-12)


class ScoringTests(unittest.TestCase):
    """Verify worked totals, missing-data handling, and plus-minus independence."""

    def test_hand_calculated_balanced_game(self):
        """Match an independently worked game with all formula terms present."""
        # Shooting: 30+4-14-0.8=19.2. Other positives:
        # 1.4+1.8+2+4.9+0.7=10.8. Penalties: 1.2+4=5.2.
        # Total: 19.2+10.8-5.2=24.8.
        self.assertEqual(calculate_game_score(example()), 24.8)

    def test_hand_calculated_free_throw_only_game(self):
        """Count made free throws through points and penalize missed free throws."""
        # Eight made of ten FT: 8-0.4*(10-8)=7.2.
        stats = dict.fromkeys(REQUIRED_FIELDS, 0)
        stats.update(points=8, free_throws_made=8, free_throws_attempted=10)
        self.assertEqual(calculate_game_score(stats), 7.2)

    def test_hand_calculated_three_point_game(self):
        """Account for threes through points without an additional shooting bonus."""
        # Four made threes on eight attempts: 12+1.6-5.6=8.0.
        stats = dict.fromkeys(REQUIRED_FIELDS, 0)
        stats.update(points=12, field_goals_made=4, field_goals_attempted=8,
                     three_pointers_made=4, three_pointers_attempted=8)
        self.assertEqual(calculate_game_score(stats), 8.0)

    def test_hand_calculated_negative_game(self):
        """Preserve a negative result rather than clamping poor games to zero."""
        # Five misses, two FT misses, four fouls, three turnovers:
        # -3.5-0.8-1.6-3=-8.9. No clamp to zero.
        stats = dict.fromkeys(REQUIRED_FIELDS, 0)
        stats.update(field_goals_attempted=5, free_throws_attempted=2,
                     personal_fouls=4, turnovers=3)
        self.assertEqual(calculate_game_score(stats), -8.9)

    def test_zero_stat_game(self):
        """Return float zero for a valid appearance with all counts zero."""
        stats = dict.fromkeys(REQUIRED_FIELDS, 0)
        self.assertEqual(calculate_game_score(stats), 0.0)
        self.assertIsInstance(calculate_game_score(stats), float)

    def test_plus_minus_is_separate_and_score_invariant(self):
        """Return plus-minus as context while keeping Game Score independent of it."""
        for plus_minus in (-50, 0, 50):
            with self.subTest(plus_minus=plus_minus):
                stats = example()
                stats["plus_minus"] = plus_minus
                result = evaluate_game_performance(stats)
                self.assertEqual(result["game_score"], 24.8)
                self.assertEqual(result["plus_minus"], plus_minus)
                self.assertEqual(result["formula_version"], FORMULA_VERSION)
                self.assertEqual(result["formula_source"], FORMULA_SOURCE)
        stats.pop("plus_minus")
        self.assertEqual(calculate_game_score(stats), 24.8)
        with self.assertRaises(ScoringValidationError):
            evaluate_game_performance(stats)

    def test_missing_required_fields_fail(self):
        """Reject each missing formula input rather than silently assuming zero."""
        for field in REQUIRED_FIELDS:
            with self.subTest(field=field):
                stats = example()
                del stats[field]
                with self.assertRaises(ScoringValidationError):
                    calculate_game_score(stats)

    def test_invalid_required_counts_fail(self):
        """Reject unsupported types, negative counts, and out-of-range integers."""
        for field in REQUIRED_FIELDS:
            for value in (None, True, "3", 3.0, 1.5, float("nan"), float("inf"), -1, 2**63):
                with self.subTest(field=field, value=value):
                    stats = example()
                    stats[field] = value
                    with self.assertRaises(ScoringValidationError):
                        calculate_game_score(stats)

    def test_invalid_plus_minus_context_fails(self):
        """Require a valid signed integer when returning plus-minus context."""
        for value in (None, True, "-12", -12.0, float("nan"), 2**63, -(2**63)-1):
            with self.subTest(value=value):
                stats = example()
                stats["plus_minus"] = value
                with self.assertRaises(ScoringValidationError):
                    evaluate_game_performance(stats)

    def test_bad_mapping_and_shot_relationships(self):
        """Reject non-mapping inputs and made shots exceeding attempted shots."""
        for value in (None, [], "stats", 1):
            with self.subTest(value=value):
                with self.assertRaises(ScoringValidationError):
                    calculate_game_score(value)
        for field in ("field_goals_made", "free_throws_made"):
            with self.subTest(field=field):
                stats = example()
                stats[field] = 99
                with self.assertRaises(ScoringValidationError):
                    calculate_game_score(stats)

    def test_inputs_unchanged_and_extra_fields_ignored(self):
        """Leave caller data intact and ignore fields outside the agreed formula."""
        stats = example()
        stats.update(ingestion_id=10, player_id="1001", true_shooting_percentage=0.99)
        before = deepcopy(stats)
        self.assertEqual(evaluate_game_performance(stats)["game_score"], 24.8)
        self.assertEqual(stats, before)


if __name__ == "__main__":
    unittest.main()
