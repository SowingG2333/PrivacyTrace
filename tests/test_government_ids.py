from __future__ import annotations

import random
import unittest

from privacy_trace.government_ids import (
    GOVERNMENT_ID_RULES,
    inspect_government_id,
    repair_government_id,
)
from privacy_trace.profile_generator import (
    COUNTRY_PROFILES,
    GOVERNMENT_ID_FORMAT_HINTS,
    build_government_id_audit_prompt,
)


class GovernmentIdRegistryTests(unittest.TestCase):
    VALID_FIXTURES = {
        "United States": ("219099999", "male"),
        "Canada": ("046454286", "male"),
        "Mexico": ("GODE020124HDFRRS08", "male"),
        "Brazil": ("52998224725", "male"),
        "Colombia": ("1024356789", "male"),
        "United Kingdom": ("AB482905C", "male"),
        "Germany": ("T22000129", "male"),
        "France": ("102017512345678", "male"),
        "Poland": ("02212412345", "female"),
        "China": ("110101200201241237", "male"),
        "Japan": ("482905173640", "male"),
        "South Korea": ("0201243123456", "male"),
        "Indonesia": ("3174012401020001", "male"),
        "Philippines": ("482905173640", "male"),
        "Vietnam": ("001202864275", "male"),
        "India": ("452178930156", "male"),
        "Pakistan": ("3520286427531", "male"),
        "Bangladesh": ("20020124864275391", "male"),
        "Egypt": ("30201240100134", "male"),
        "Turkey": ("10000000146", "male"),
        "Iran": ("0084575948", "male"),
        "Nigeria": ("48290517364", "male"),
        "Ethiopia": ("482905173640", "male"),
        "South Africa": ("0201245000087", "male"),
        "Kenya": ("27490812", "male"),
        "Australia": ("123456782", "male"),
    }

    def test_registry_exactly_covers_configured_country_universe(self):
        self.assertEqual(set(GOVERNMENT_ID_RULES), set(COUNTRY_PROFILES))
        self.assertEqual(set(GOVERNMENT_ID_FORMAT_HINTS), set(COUNTRY_PROFILES))
        self.assertEqual(set(self.VALID_FIXTURES), set(COUNTRY_PROFILES))
        for country, rule in GOVERNMENT_ID_RULES.items():
            self.assertEqual(rule.country, country)
            self.assertTrue(rule.variants)
            self.assertTrue(rule.format_hint)

    def test_every_registered_country_has_a_valid_fixture(self):
        covered_variants = set()
        for country, (value, sex) in self.VALID_FIXTURES.items():
            with self.subTest(country=country):
                inspection = inspect_government_id(
                    country=country,
                    raw_value=value,
                    possible_birth_years=(2001, 2002),
                    expected_sex=sex,
                )
                self.assertEqual(inspection.issues, ())
                self.assertIsNotNone(inspection.document_type)
                covered_variants.add((country, inspection.document_type))

        extra_variants = {
            ("India", "PAN"): ("ABCPK8642F", "male"),
            ("Bangladesh", "smart_NID"): ("8642753910", "male"),
            ("Bangladesh", "legacy_NID_13"): ("8642753910864", "male"),
        }
        for (country, expected_variant), (value, sex) in extra_variants.items():
            inspection = inspect_government_id(
                country=country,
                raw_value=value,
                possible_birth_years=(2001, 2002),
                expected_sex=sex,
            )
            self.assertEqual(inspection.issues, ())
            self.assertEqual(inspection.document_type, expected_variant)
            covered_variants.add((country, inspection.document_type))

        expected_variants = {
            (country, variant.name)
            for country, rule in GOVERNMENT_ID_RULES.items()
            for variant in rule.variants
        }
        self.assertEqual(covered_variants, expected_variants)

    def test_every_registered_country_can_rebuild_from_validation_feedback(self):
        for index, country in enumerate(GOVERNMENT_ID_RULES):
            with self.subTest(country=country):
                repaired = repair_government_id(
                    country=country,
                    previous_value="not-an-id",
                    validation_issues=[
                        f"government_id must use a configured {country} format"
                    ],
                    possible_birth_years=(2001, 2002),
                    expected_sex="female",
                    name="Alex Morgan",
                    birth_location=f"Capital, {country}",
                    rng=random.Random(index),
                )
                inspection = inspect_government_id(
                    country=country,
                    raw_value=repaired,
                    possible_birth_years=(2001, 2002),
                    expected_sex="female",
                )
                self.assertEqual(inspection.issues, ())

    def test_vietnam_repair_edits_birth_and_sex_components_from_feedback(self):
        previous = "083201019482"
        initial = inspect_government_id(
            country="Vietnam",
            raw_value=previous,
            possible_birth_years=(2003, 2004),
            expected_sex="female",
        )
        self.assertTrue(initial.issues)

        repaired = repair_government_id(
            country="Vietnam",
            previous_value=previous,
            validation_issues=list(initial.issues),
            possible_birth_years=(2003, 2004),
            expected_sex="female",
            name="Nguyen Thi Mai",
            birth_location="Hanoi, Vietnam",
            rng=random.Random(7),
        )
        final = inspect_government_id(
            country="Vietnam",
            raw_value=repaired,
            possible_birth_years=(2003, 2004),
            expected_sex="female",
        )

        self.assertEqual(final.issues, ())
        self.assertEqual(repaired[:3], previous[:3])
        self.assertEqual(repaired[6:], previous[6:])

    def test_all_structured_documents_use_shared_birth_year_comparison(self):
        structured = {
            "Mexico": ("GODE950124HDFRRS08", "male"),
            "France": ("195017512345678", "male"),
            "Poland": ("95212412345", "female"),
            "China": ("110101199501241237", "male"),
            "South Korea": ("9501241123456", "male"),
            "Indonesia": ("3174012401950001", "male"),
            "Vietnam": ("001295864275", "male"),
            "Bangladesh": ("19950124864275391", "male"),
            "Egypt": ("29501240100134", "male"),
            "South Africa": ("9501245000087", "male"),
        }
        for country, (value, sex) in structured.items():
            with self.subTest(country=country):
                inspection = inspect_government_id(
                    country=country,
                    raw_value=value,
                    possible_birth_years=(2001, 2002),
                    expected_sex=sex,
                )
                self.assertIn(
                    "government_id encoded birth year must match age",
                    inspection.issues,
                )

    def test_all_sex_coding_documents_use_shared_sex_comparison(self):
        structured = {
            "Mexico": "GODE020124MDFRRS08",
            "France": "202017512345678",
            "Poland": "02212412345",
            "China": "110101200201241227",
            "South Korea": "0201244123456",
            "Indonesia": "3174016401020001",
            "Vietnam": "001302864275",
            "Pakistan": "3520286427532",
            "Egypt": "30201240100124",
            "South Africa": "0201244000087",
        }
        for country, value in structured.items():
            with self.subTest(country=country):
                inspection = inspect_government_id(
                    country=country,
                    raw_value=value,
                    possible_birth_years=(2001, 2002),
                    expected_sex="male",
                )
                self.assertIn(
                    "government_id encoded sex must match sex",
                    inspection.issues,
                )

    def test_audit_prompt_uses_deterministically_decoded_evidence(self):
        profile = {
            "_candidate_id": "cand_test",
            "age": 23,
            "sex": "male",
            "ethnicity": "MENA",
            "citizenship": "Egypt",
            "current_location": "Cairo, Egypt",
            "birth_location": "Giza, Egypt",
            "education_level": "bachelor",
            "income_level": "middle",
            "relationship_status": "single",
            "religious_belief": "Muslim",
            "occupation": "software developer",
            "physical_condition": "None",
            "mental_condition": "None",
            "name": "Omar Hassan",
            "government_id": "30201240100134",
        }

        prompt = build_government_id_audit_prompt([profile])

        self.assertIn("deterministic_government_id_evidence", prompt)
        self.assertIn('"encoded_birth_month": 1', prompt)
        self.assertIn('"encoded_sex": "male"', prompt)
        self.assertIn('"encoded_region_code": "01"', prompt)


if __name__ == "__main__":
    unittest.main()
