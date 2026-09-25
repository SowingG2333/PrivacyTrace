import unittest
from collections import defaultdict
from unittest.mock import patch

import pandas

from privacy_trace.profile_generator import (
    COUNTRY_PROFILES,
    MENTAL_CONDITIONS,
    OCCUPATION_GROUP_TO_OPTIONS,
    OCCUPATION_TO_GROUP,
    OCCUPATIONS,
    PHYSICAL_CONDITIONS,
    build_default_targets,
    skeleton_ipf_coverage,
)
from scripts.rebuild_official_ipf_targets import (
    MENTAL_CAUSE_IDS,
    MENTAL_CONDITION_STATISTICS,
    MENTAL_CONDITIONS as TARGET_MENTAL_CONDITIONS,
    PHYSICAL_CAUSE_IDS,
    PHYSICAL_CONDITION_STATISTICS,
    PHYSICAL_CONDITIONS as TARGET_PHYSICAL_CONDITIONS,
    INCOME_LEVELS,
    ILO_EDUCATION_LEVEL_TO_GROUP,
    WELFARE_EDUCATION_TYPES,
    WELFARE_EDUCATION_LEVEL_TO_GROUP,
    build_education_income_targets,
    build_income_targets,
    build_one_label_health_targets,
    latest_national_pip_row,
    rake_matrix,
)


class RelativeIncomeTargetTests(unittest.TestCase):
    def test_rake_matrix_matches_both_requested_margins(self):
        matrix = rake_matrix(
            {
                ("young", "employed"): 8.0,
                ("young", "not employed"): 2.0,
                ("older", "employed"): 3.0,
                ("older", "not employed"): 7.0,
            },
            {"young": 0.6, "older": 0.4},
            {"employed": 0.55, "not employed": 0.45},
        )

        self.assertAlmostEqual(
            sum(matrix[("young", column)] for column in ("employed", "not employed")),
            0.6,
        )
        self.assertAlmostEqual(
            sum(matrix[("older", column)] for column in ("employed", "not employed")),
            0.4,
        )
        self.assertAlmostEqual(
            sum(matrix[(row, "employed")] for row in ("young", "older")),
            0.55,
        )
        self.assertAlmostEqual(
            sum(matrix[(row, "not employed")] for row in ("young", "older")),
            0.45,
        )

    def test_education_income_transfer_preserves_margins_and_order(self):
        source_rows = pandas.DataFrame(
            [
                {
                    "country_code": "EXP",
                    "region": "OHI",
                    "type": source_type,
                    "hc215": hc215,
                    "hc685": hc685,
                    "pop": 100.0,
                }
                for source_type, hc215, hc685 in (
                    ("No education", 0.80, 0.95),
                    ("Primary (complete or incomplete)", 0.50, 0.80),
                    ("Secondary (complete or incomplete)", 0.20, 0.50),
                    ("Tertiary (complete or incomplete)", 0.05, 0.20),
                )
            ]
        )
        education_targets = {
            "Example | none": 0.25,
            "Example | primary": 0.25,
            "Example | lower secondary": 0.0,
            "Example | upper secondary": 0.25,
            "Example | vocational": 0.0,
            "Example | bachelor": 0.25,
            "Example | master": 0.0,
            "Example | doctorate": 0.0,
        }
        income_targets = {
            f"Example | {level}": 0.20 for level in INCOME_LEVELS
        }

        with patch(
            "scripts.rebuild_official_ipf_targets.COUNTRY_CODES",
            {"Example": {"iso3": "EXP"}},
        ):
            targets, audit = build_education_income_targets(
                country_mass={"Example": 1.0},
                education_targets=education_targets,
                income_targets=income_targets,
                welfare_rows=source_rows,
            )

        for group in WELFARE_EDUCATION_TYPES:
            self.assertAlmostEqual(
                sum(
                    targets[f"Example | {group} | {level}"]
                    for level in INCOME_LEVELS
                ),
                0.25,
            )
        for level in INCOME_LEVELS:
            self.assertAlmostEqual(
                sum(
                    targets[f"Example | {group} | {level}"]
                    for group in WELFARE_EDUCATION_TYPES
                ),
                0.20,
            )
        mean_income_rank = {
            group: sum(
                index * targets[f"Example | {group} | {level}"]
                for index, level in enumerate(INCOME_LEVELS)
            )
            / 0.25
            for group in WELFARE_EDUCATION_TYPES
        }
        self.assertLess(
            mean_income_rank["no education"], mean_income_rank["primary"]
        )
        self.assertLess(
            mean_income_rank["primary"], mean_income_rank["secondary"]
        )
        self.assertLess(
            mean_income_rank["secondary"], mean_income_rank["tertiary"]
        )
        self.assertLess(audit["Example"]["maximum_margin_error"], 1e-8)

    def test_sparse_direct_welfare_group_uses_region_relation(self):
        rows = []
        for country_code, no_education_pop, no_education_hc685 in (
            ("EXP", 0.001, 0.05),
            ("REF", 100.0, 0.95),
        ):
            for source_type, hc215, hc685, population in (
                (
                    "No education",
                    0.01 if country_code == "EXP" else 0.80,
                    no_education_hc685,
                    no_education_pop,
                ),
                ("Primary (complete or incomplete)", 0.50, 0.80, 100.0),
                ("Secondary (complete or incomplete)", 0.20, 0.50, 100.0),
                ("Tertiary (complete or incomplete)", 0.05, 0.20, 100.0),
            ):
                rows.append(
                    {
                        "country_code": country_code,
                        "region": "SYN",
                        "type": source_type,
                        "hc215": hc215,
                        "hc685": hc685,
                        "pop": population,
                    }
                )
        source_rows = pandas.DataFrame(rows)
        education_targets = {
            "Example | none": 0.25,
            "Example | primary": 0.25,
            "Example | lower secondary": 0.0,
            "Example | upper secondary": 0.25,
            "Example | vocational": 0.0,
            "Example | bachelor": 0.25,
            "Example | master": 0.0,
            "Example | doctorate": 0.0,
        }
        income_targets = {
            f"Example | {level}": 0.20 for level in INCOME_LEVELS
        }

        with patch(
            "scripts.rebuild_official_ipf_targets.COUNTRY_CODES",
            {"Example": {"iso3": "EXP"}},
        ):
            _, audit = build_education_income_targets(
                country_mass={"Example": 1.0},
                education_targets=education_targets,
                income_targets=income_targets,
                welfare_rows=source_rows,
            )

        example = audit["Example"]
        self.assertTrue(
            example["source_provenance"]["no education"].startswith(
                "world_bank_region_proxy:SYN:"
            )
        )
        self.assertTrue(
            example["source_provenance"]["primary"].startswith(
                "world_bank_region_proxy:SYN:"
            )
        )
        self.assertFalse(example["country_dimension_direct_accepted"])
        self.assertGreater(
            example["conditioned_headcounts"]["no education"]["hc685"],
            0.90,
        )
        self.assertLess(
            example["source_to_conditioned_share_ratios"]["no education"],
            0.1,
        )

    def test_expanded_joint_constraint_signatures_are_present(self):
        constraints = {item.name: item.fields for item in build_default_targets()}
        expected = {
            "current_country_x_age_group_x_sex": (
                "current_country",
                "age_group",
                "sex",
            ),
            "current_country_x_sex_x_birth_migration_status": (
                "current_country",
                "sex",
                "birth_migration_status",
            ),
            "current_country_x_sex_x_citizenship_migration_status": (
                "current_country",
                "sex",
                "citizenship_migration_status",
            ),
            "current_country_x_age_group_x_sex_x_relationship_status": (
                "current_country",
                "age_group",
                "sex",
                "relationship_status",
            ),
            "current_country_x_age_group_x_sex_x_ilo_education_group": (
                "current_country",
                "age_group",
                "sex",
                "ilo_education_group",
            ),
            "current_country_x_age_group_x_sex_x_employment_status": (
                "current_country",
                "age_group",
                "sex",
                "employment_status",
            ),
            "current_country_x_sex_x_ilo_education_group_x_employment_status": (
                "current_country",
                "sex",
                "ilo_education_group",
                "employment_status",
            ),
            "current_country_x_ilo_education_group_x_occupation_group": (
                "current_country",
                "ilo_education_group",
                "occupation_group",
            ),
            "current_country_x_welfare_education_group_x_income_level": (
                "current_country",
                "welfare_education_group",
                "income_level",
            ),
            "current_country_x_age_stage_x_income_level": (
                "current_country",
                "age_stage",
                "income_level",
            ),
            "current_country_x_age_stage_x_sex_x_occupation_group": (
                "current_country",
                "age_stage",
                "sex",
                "occupation_group",
            ),
            "current_country_x_relationship_status_x_employment_status": (
                "current_country",
                "relationship_status",
                "employment_status",
            ),
            "current_country_x_age_group_x_sex_x_physical_condition": (
                "current_country",
                "age_group",
                "sex",
                "physical_condition",
            ),
            "current_country_x_age_group_x_sex_x_mental_condition": (
                "current_country",
                "age_group",
                "sex",
                "mental_condition",
            ),
        }
        for name, fields in expected.items():
            self.assertEqual(constraints.get(name), fields, name)

    def test_expanded_joint_constraints_preserve_shared_margins(self):
        constraints = {
            item.name: item.targets for item in build_default_targets()
        }
        for name, targets in constraints.items():
            self.assertAlmostEqual(sum(targets.values()), 1.0, places=7, msg=name)
            self.assertGreaterEqual(min(targets.values()), 0.0, name)

        def aggregate(
            targets: dict[str, float],
            keep: tuple[int, ...],
            transform: dict[int, dict[str, str]] | None = None,
        ) -> dict[tuple[str, ...], float]:
            output: dict[tuple[str, ...], float] = defaultdict(float)
            for key, value in targets.items():
                parts = key.split(" | ")
                mapped = [
                    (transform or {}).get(index, {}).get(parts[index], parts[index])
                    for index in keep
                ]
                output[tuple(mapped)] += value
            return dict(output)

        def assert_same(
            left: dict[tuple[str, ...], float],
            right: dict[tuple[str, ...], float],
            label: str,
        ) -> None:
            self.assertEqual(set(left), set(right), label)
            for key in left:
                self.assertAlmostEqual(
                    left[key], right[key], places=7, msg=f"{label}: {key}"
                )

        country_age_sex = aggregate(
            constraints["current_country_x_age_group_x_sex"], (0, 1, 2)
        )
        for name in (
            "current_country_x_age_group_x_sex_x_relationship_status",
            "current_country_x_age_group_x_sex_x_ilo_education_group",
            "current_country_x_age_group_x_sex_x_employment_status",
            "current_country_x_age_group_x_sex_x_physical_condition",
            "current_country_x_age_group_x_sex_x_mental_condition",
        ):
            assert_same(
                country_age_sex,
                aggregate(constraints[name], (0, 1, 2)),
                name,
            )

        country_sex_education = aggregate(
            constraints[
                "current_country_x_age_group_x_sex_x_ilo_education_group"
            ],
            (0, 2, 3),
        )
        assert_same(
            country_sex_education,
            aggregate(
                constraints[
                    "current_country_x_sex_x_ilo_education_group_x_employment_status"
                ],
                (0, 1, 2),
            ),
            "education x employment education margin",
        )

        detailed_education = aggregate(
            constraints["country_x_education"],
            (0, 1),
            {1: ILO_EDUCATION_LEVEL_TO_GROUP},
        )
        assert_same(
            detailed_education,
            aggregate(
                constraints[
                    "current_country_x_ilo_education_group_x_occupation_group"
                ],
                (0, 1),
            ),
            "education x occupation education margin",
        )
        assert_same(
            aggregate(
                constraints["current_country_x_sex_x_occupation"], (0, 2)
            ),
            aggregate(
                constraints[
                    "current_country_x_ilo_education_group_x_occupation_group"
                ],
                (0, 2),
            ),
            "education x occupation occupation margin",
        )

        welfare_education = aggregate(
            constraints["country_x_education"],
            (0, 1),
            {1: WELFARE_EDUCATION_LEVEL_TO_GROUP},
        )
        education_income = constraints[
            "current_country_x_welfare_education_group_x_income_level"
        ]
        assert_same(
            welfare_education,
            aggregate(education_income, (0, 1)),
            "education x income education margin",
        )
        assert_same(
            aggregate(constraints["country_x_income_level"], (0, 1)),
            aggregate(education_income, (0, 2)),
            "education x income income margin",
        )

        age_stage = aggregate(
            constraints["current_country_x_age_group_x_sex"],
            (0, 1),
            {
                1: {
                    "18-24": "youth",
                    "25-34": "adult",
                    "35-44": "adult",
                    "45-54": "adult",
                    "55-64": "adult",
                    "65-80": "adult",
                }
            },
        )
        age_income = constraints[
            "current_country_x_age_stage_x_income_level"
        ]
        assert_same(
            age_stage,
            aggregate(age_income, (0, 1)),
            "age stage x income age margin",
        )
        assert_same(
            aggregate(constraints["country_x_income_level"], (0, 1)),
            aggregate(age_income, (0, 2)),
            "age stage x income income margin",
        )

        age_stage_occupation = constraints[
            "current_country_x_age_stage_x_sex_x_occupation_group"
        ]
        assert_same(
            aggregate(
                constraints["current_country_x_sex_x_occupation"], (0, 1, 2)
            ),
            aggregate(age_stage_occupation, (0, 2, 3)),
            "age stage x occupation occupation margin",
        )
        assert_same(
            aggregate(
                constraints[
                    "current_country_x_age_group_x_sex_x_employment_status"
                ],
                (0, 1, 2, 3),
                {
                    1: {
                        "18-24": "youth",
                        "25-34": "adult",
                        "35-44": "adult",
                        "45-54": "adult",
                        "55-64": "adult",
                        "65-80": "adult",
                    },
                    3: {"employed": "employed", "not employed": "None"},
                },
            ),
            aggregate(
                age_stage_occupation,
                (0, 1, 2, 3),
                {
                    3: {
                        occupation: (
                            "None" if occupation == "None" else "employed"
                        )
                        for occupation in OCCUPATION_GROUP_TO_OPTIONS
                    }
                },
            ),
            "age stage x occupation employment margin",
        )

        relationship_employment = constraints[
            "current_country_x_relationship_status_x_employment_status"
        ]
        assert_same(
            aggregate(
                constraints[
                    "current_country_x_age_group_x_sex_x_relationship_status"
                ],
                (0, 3),
            ),
            aggregate(relationship_employment, (0, 1)),
            "relationship x employment relationship margin",
        )
        assert_same(
            aggregate(
                constraints[
                    "current_country_x_age_group_x_sex_x_employment_status"
                ],
                (0, 3),
            ),
            aggregate(relationship_employment, (0, 2)),
            "relationship x employment employment margin",
        )

        country_sex = aggregate(
            constraints["current_country_x_age_group_x_sex"], (0, 2)
        )
        for name in (
            "current_country_x_sex_x_birth_migration_status",
            "current_country_x_sex_x_citizenship_migration_status",
        ):
            assert_same(
                country_sex,
                aggregate(constraints[name], (0, 1)),
                name,
            )

    def test_expanded_country_universe_has_complete_country_constraint_support(self):
        constraints = build_default_targets()
        country_names = set(COUNTRY_PROFILES)

        self.assertEqual(len(country_names), 26)
        self.assertGreaterEqual(
            sum(len(profile["locations"]) for profile in COUNTRY_PROFILES.values()),
            8_000,
        )
        for constraint in constraints:
            if "current_country" not in constraint.fields:
                continue
            countries_with_positive_support = {
                key.split(" | ", 1)[0]
                for key, value in constraint.targets.items()
                if value > 0
            }
            self.assertEqual(
                countries_with_positive_support,
                country_names,
                constraint.name,
            )
        self.assertTrue(all(skeleton_ipf_coverage(constraints).values()))

    def test_expanded_schema_options_match_their_calibration_structure(self):
        self.assertEqual(len(OCCUPATIONS), 116)
        self.assertEqual(len(PHYSICAL_CONDITIONS), 23)
        self.assertEqual(len(MENTAL_CONDITIONS), 17)
        self.assertEqual(set(OCCUPATION_TO_GROUP), set(OCCUPATIONS))
        self.assertEqual(len(OCCUPATIONS), len(set(OCCUPATIONS)))
        for group, options in OCCUPATION_GROUP_TO_OPTIONS.items():
            if group == "None":
                self.assertEqual(options, ["None"])
                continue
            self.assertGreaterEqual(len(options), 9)
            self.assertNotIn(group, options)
            self.assertTrue(all(len(option) <= 32 for option in options))
        self.assertEqual(PHYSICAL_CONDITIONS, TARGET_PHYSICAL_CONDITIONS)
        self.assertEqual(MENTAL_CONDITIONS, TARGET_MENTAL_CONDITIONS)

        constraints = {
            item.name: item
            for item in build_default_targets()
        }
        self.assertEqual(
            constraints[
                "current_country_x_age_group_x_sex_x_physical_condition"
            ].fields,
            ("current_country", "age_group", "sex", "physical_condition"),
        )
        self.assertEqual(
            constraints[
                "current_country_x_age_group_x_sex_x_mental_condition"
            ].fields,
            ("current_country", "age_group", "sex", "mental_condition"),
        )

    def test_health_options_have_statistical_span_metadata(self):
        self.assertEqual(
            set(PHYSICAL_CONDITIONS) - {"None"},
            set(PHYSICAL_CONDITION_STATISTICS),
        )
        self.assertEqual(
            set(MENTAL_CONDITIONS) - {"None"},
            set(MENTAL_CONDITION_STATISTICS),
        )
        for statistic in PHYSICAL_CONDITION_STATISTICS.values():
            self.assertTrue(statistic["gbd_cause_ids"])
            self.assertEqual(statistic["estimate_year"], 2023)
            self.assertTrue(statistic["system"])
            self.assertTrue(statistic["scope"])
            self.assertTrue(statistic["source_key"])
        for statistic in MENTAL_CONDITION_STATISTICS.values():
            self.assertTrue(statistic["gbd_cause_ids"])
            self.assertEqual(statistic["estimate_year"], 2023)
            self.assertTrue(statistic["family"])
            self.assertTrue(statistic["scope"])
            self.assertTrue(statistic["source_key"])

    def test_latest_pip_row_prefers_observed_distribution(self):
        rows = [
            {
                "reporting_level": "national",
                "reporting_year": 2024,
                "survey_year": 2022,
                "headcount": 0.2,
                "median": 10.0,
                "is_interpolated": True,
            },
            {
                "reporting_level": "national",
                "reporting_year": 2023,
                "survey_year": 2023,
                "headcount": 0.2,
                "median": 9.0,
                "is_interpolated": False,
            },
        ]

        selected = latest_national_pip_row(rows, require_median=True)

        self.assertEqual(selected["reporting_year"], 2023)
        self.assertFalse(selected["is_interpolated"])

    def test_income_targets_are_headcount_differences(self):
        pip_rows = {
            "Example": {
                "reporting_year": 2023,
                "survey_year": 2023,
                "is_interpolated": False,
                "welfare_type": "income",
                "median_2021_ppp_usd_per_day": 10.0,
                "thresholds_2021_ppp_usd_per_day": {
                    "0.75": 7.5,
                    "1.0": 10.0,
                    "1.5": 15.0,
                    "2.0": 20.0,
                },
                "headcounts": {
                    "0.75": 0.30,
                    "1.0": 0.50,
                    "1.5": 0.75,
                    "2.0": 0.90,
                },
            }
        }

        targets, audit = build_income_targets({"Example": 1.0}, pip_rows)

        expected = {
            "low": 0.30,
            "lower-middle": 0.20,
            "middle": 0.25,
            "upper-middle": 0.15,
            "high": 0.10,
        }
        for label, share in expected.items():
            self.assertAlmostEqual(targets[f"Example | {label}"], share)
            self.assertAlmostEqual(
                audit["Example"]["income_level_shares"][label], share
            )

    def test_country_age_sex_health_targets_preserve_source_rates_and_margin(self):
        rates = {}
        for sex in ("female", "male"):
            rates[("Example", 8, sex, 1)] = 0.0
            rates[("Example", 9, sex, 1)] = 14_000.0
            rates[("Example", 8, sex, 2)] = 20_000.0
            rates[("Example", 9, sex, 2)] = 20_000.0
        wpp = {
            "age5_sex": {
                ("Example", 2025, 15, 19, sex): 100.0
                for sex in ("female", "male")
            }
        }
        for sex in ("female", "male"):
            wpp["age5_sex"][("Example", 2025, 20, 24, sex)] = 100.0
        country_age_sex = {
            f"Example | 18-24 | {sex}": 0.5
            for sex in ("female", "male")
        }

        with (
            patch(
                "scripts.rebuild_official_ipf_targets.COUNTRY_CODES",
                {"Example": {"iso3": "EXP"}},
            ),
            patch(
                "scripts.rebuild_official_ipf_targets.AGE_GROUPS",
                {"18-24": (18, 24)},
            ),
            patch(
                "scripts.rebuild_official_ipf_targets.GBD_AGE_ID_TO_RANGE",
                {8: (15, 19), 9: (20, 24)},
            ),
        ):
            targets, audit = build_one_label_health_targets(
                rates=rates,
                wpp=wpp,
                country_age_sex_targets=country_age_sex,
                condition_cause_ids={"A": (1,), "B": (2,)},
                condition_statistics={"A": {}, "B": {}},
            )

        for sex in ("female", "male"):
            prefix = f"Example | 18-24 | {sex}"
            demographic_total = sum(
                value
                for key, value in targets.items()
                if key.startswith(prefix + " | ")
            )
            self.assertAlmostEqual(demographic_total, 0.5)
            # 18-19 contributes 2/5 of its source bin: (0*40 + 14000*100)/140.
            self.assertAlmostEqual(
                audit["country_age_sex_prevalence_per_100k"][prefix]["A"],
                10_000.0,
            )
            self.assertAlmostEqual(
                audit["country_age_sex_prevalence_per_100k"][prefix]["B"],
                20_000.0,
            )
            self.assertAlmostEqual(
                targets[f"{prefix} | None"] / demographic_total,
                (1.0 - 0.10) * (1.0 - 0.20),
            )
            self.assertAlmostEqual(
                targets[f"{prefix} | A"] / targets[f"{prefix} | B"],
                0.5,
            )

    def test_ihme_mapping_has_exactly_40_non_overlapping_source_causes(self):
        physical_ids = {
            cause_id
            for cause_ids in PHYSICAL_CAUSE_IDS.values()
            for cause_id in cause_ids
        }
        mental_ids = {
            cause_id
            for cause_ids in MENTAL_CAUSE_IDS.values()
            for cause_id in cause_ids
        }
        self.assertFalse(physical_ids & mental_ids)
        self.assertEqual(len(physical_ids | mental_ids), 40)
        self.assertEqual(MENTAL_CAUSE_IDS["depressive disorders"], (568, 569))
        self.assertEqual(MENTAL_CAUSE_IDS["eating disorders"], (573, 574))

if __name__ == "__main__":
    unittest.main()
