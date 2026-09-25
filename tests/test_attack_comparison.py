import unittest

from scripts.analyze_attack_comparison import (
    canonical_prediction,
    normalized_match,
    strict_match,
)


class AttackComparisonMatchingTests(unittest.TestCase):
    def test_strict_matching_normalizes_format_but_not_semantics(self):
        self.assertTrue(strict_match("income_level", "lower middle", "lower-middle"))
        self.assertTrue(strict_match("age", "50", 50))
        self.assertFalse(strict_match("age", "45-55", 50))
        self.assertFalse(strict_match("citizenship", "Indian", "India"))

    def test_schema_matching_handles_documented_aliases(self):
        self.assertTrue(normalized_match("citizenship", "Indian", "India"))
        self.assertTrue(normalized_match("ethnicity", "Tamil", "South Asian"))
        self.assertTrue(normalized_match("religious_belief", "Catholic", "Christian"))
        self.assertTrue(normalized_match("religious_belief", "None", "Unaffiliated"))
        self.assertTrue(
            normalized_match(
                "current_location",
                "Bengaluru, Karnataka, India",
                "Bengaluru, India",
            )
        )
        self.assertTrue(normalized_match("age", "45-55", 50))

    def test_prediction_agreement_is_independent_of_ground_truth(self):
        self.assertEqual(
            canonical_prediction("religious_belief", "Catholic"),
            canonical_prediction("religious_belief", "Christian"),
        )
        self.assertNotEqual(
            canonical_prediction("age", "30"),
            canonical_prediction("age", "35"),
        )


if __name__ == "__main__":
    unittest.main()
