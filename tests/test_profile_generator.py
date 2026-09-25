import json
import os
import subprocess
import sys
import unittest

from privacy_trace.profile_generator import (
    GeneratorConfig,
    IPFConstraint,
    OCCUPATIONS,
    REAL_TARGETS_PATH,
    SyntheticProfileGenerator,
    distribution_l1,
    make_key,
    skeleton_ipf_coverage,
    weighted_distribution,
)


class SyntheticProfileGeneratorTests(unittest.TestCase):
    def test_default_targets_cover_every_skeleton_field(self):
        generator = SyntheticProfileGenerator(GeneratorConfig(count=1, seed=11))

        coverage = skeleton_ipf_coverage(generator.targets)

        self.assertTrue(all(coverage.values()), coverage)

    def test_default_targets_replace_placeholder_constraints(self):
        generator = SyntheticProfileGenerator(GeneratorConfig(count=1, seed=11))

        names = {constraint.name for constraint in generator.targets}

        self.assertFalse(
            {
                "citizenship_country_population",
                "birth_country_population",
                "ethnicity",
                "income_level",
                "relationship_status",
            }
            & names
        )
        self.assertTrue(
            {
                "current_country_x_citizenship_country",
                "current_country_x_birth_country",
                "current_country_x_ethnicity",
                "country_x_income_level",
                "age_x_sex_x_relationship_status",
                "current_country_x_sex_x_occupation",
                "current_country_x_age_group_x_sex_x_physical_condition",
                "current_country_x_age_group_x_sex_x_mental_condition",
            }
            <= names
        )

    def test_default_target_metadata_has_no_prior_fallback(self):
        with open(REAL_TARGETS_PATH, "r", encoding="utf-8") as handle:
            metadata = json.load(handle)["metadata"]

        self.assertFalse(
            any(
                "prior_fallback" in status
                for status in metadata["static_target_status"].values()
            )
        )
        self.assertEqual(
            metadata["static_target_status"]["country_x_religion"],
            "ready_authority_backed_non_official",
        )
        self.assertEqual(
            metadata["schema_option_set"]["occupation"],
            OCCUPATIONS,
        )

    def test_ipf_moves_weighted_distribution_toward_constraint(self):
        generator = SyntheticProfileGenerator(
            GeneratorConfig(
                count=10,
                seed=3,
                ipf_iterations=20,
                require_full_skeleton_ipf=False,
            ),
            targets=[
                IPFConstraint(
                    "sex_balance",
                    ("sex",),
                    {make_key(["female"]): 0.8, make_key(["male"]): 0.2},
                )
            ],
        )
        candidates = generator.generate_candidates(200)
        before_weights = [1.0 for _ in candidates]
        before = weighted_distribution(candidates, before_weights, ("sex",))
        before_distance = distribution_l1(
            before,
            {make_key(["female"]): 0.8, make_key(["male"]): 0.2},
        )

        generator.apply_ipf(candidates)
        after_weights = [candidate["_weight"] for candidate in candidates]
        after = weighted_distribution(candidates, after_weights, ("sex",))
        after_distance = distribution_l1(
            after,
            {make_key(["female"]): 0.8, make_key(["male"]): 0.2},
        )

        self.assertLess(after_distance, before_distance)
        self.assertLess(after_distance, 0.05)

    def test_ipf_honors_minimum_iterations_before_stopping(self):
        generator = SyntheticProfileGenerator(
            GeneratorConfig(
                count=10,
                seed=3,
                ipf_iterations=5,
                ipf_min_iterations=5,
                require_full_skeleton_ipf=False,
            ),
            targets=[
                IPFConstraint(
                    "sex_balance",
                    ("sex",),
                    {make_key(["female"]): 0.8, make_key(["male"]): 0.2},
                )
            ],
        )
        candidates = generator.generate_candidates(200)

        report = generator.apply_ipf(candidates)

        self.assertTrue(report["converged"])
        self.assertEqual(report["iterations"], 5)

    def test_fixed_seed_is_stable_across_python_hash_seeds(self):
        code = """
import hashlib
import json
from privacy_trace.profile_generator import GeneratorConfig, SyntheticProfileGenerator
generator = SyntheticProfileGenerator(
    GeneratorConfig(count=30, seed=20260729, skip_ipf=True)
)
rows = generator.generate_candidates(30)
payload = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()
print(hashlib.sha256(payload).hexdigest())
"""
        digests = []
        for hash_seed in ("1", "2"):
            environment = dict(os.environ)
            environment["PYTHONHASHSEED"] = hash_seed
            completed = subprocess.run(
                [sys.executable, "-c", code],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            digests.append(completed.stdout.strip())
        self.assertEqual(digests[0], digests[1])


if __name__ == "__main__":
    unittest.main()
