import copy
from collections import deque
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_env.recovery import ProviderHealthExceeded, RollingFailureWindow
from privacy_trace.profile_generator import (
    COUNTRY_PROFILES,
    FULL_SCHEMA_FIELDS,
    NATIONAL_PHONE_LENGTH_OPTIONS,
    SKELETON_FIELDS,
    GeneratorConfig,
    IPFConstraint,
    SyntheticProfileGenerator,
    build_arg_parser,
    build_completion_prompt,
    build_government_id_audit_prompt,
    build_pii_repair_prompt,
    build_seed_audit_prompt,
    bucket_value,
    check_profile,
    make_key,
    pii_field_issues,
    prune_old_profile_generation_runs,
    strip_internal_fields,
    write_pii_diagnostics,
)


def make_seed(candidate_id: str, **overrides):
    seed = {
        "age": 34,
        "sex": "female",
        "ethnicity": "East Asian",
        "citizenship": "Canada",
        "current_location": "Toronto, Canada",
        "birth_location": "Tokyo, Japan",
        "education_level": "bachelor",
        "income_level": "middle",
        "relationship_status": "widowed",
        "religious_belief": "Folk",
        "occupation": "civil engineer",
        "physical_condition": "None",
        "mental_condition": "None",
        "_citizenship_country": "Canada",
        "_current_country": "Canada",
        "_birth_country": "Japan",
        "_candidate_id": candidate_id,
    }
    seed.update(overrides)
    if "citizenship" in overrides:
        seed["_citizenship_country"] = seed["citizenship"]
    if "current_location" in overrides:
        seed["_current_country"] = seed["current_location"].rsplit(",", 1)[-1].strip()
    if "birth_location" in overrides:
        seed["_birth_country"] = seed["birth_location"].rsplit(",", 1)[-1].strip()
    return seed


def stub_index(candidate_id: str) -> int:
    return int.from_bytes(
        hashlib.sha256(candidate_id.encode("utf-8")).digest()[:8],
        "big",
    )


def stub_digits(candidate_id: str, length: int) -> str:
    value = str(stub_index(candidate_id))
    return (value * ((length // len(value)) + 1))[:length]


def stub_phone(item: dict[str, object]) -> str:
    country = str(item["citizenship"])
    national_length = min(NATIONAL_PHONE_LENGTH_OPTIONS[country])
    return (
        f"{COUNTRY_PROFILES[country]['phone_code']} "
        f"{stub_digits(str(item['candidate_id']), national_length)}"
    )


def stub_government_id(item: dict[str, object]) -> str:
    candidate_id = str(item["candidate_id"])
    country = str(item["citizenship"])
    age = int(item["age"])
    sex = str(item["sex"])
    birth_year = 2025 - age
    serial = stub_index(candidate_id)
    sex_digit = 1 if sex == "male" else 2
    if country == "France":
        return (
            f"{1 if sex == 'male' else 2}{birth_year % 100:02d}01"
            f"75123{serial % 1000:03d}{serial % 100:02d}"
        )
    if country == "Poland":
        encoded_month = 21 if birth_year >= 2000 else 1
        return (
            f"{birth_year % 100:02d}{encoded_month:02d}01"
            f"{serial % 1000:03d}{sex_digit}{serial % 10}"
        )
    if country == "South Korea":
        marker = (
            3 if birth_year >= 2000 and sex == "male"
            else 4 if birth_year >= 2000
            else 1 if sex == "male"
            else 2
        )
        return f"{birth_year % 100:02d}0101{marker}{serial % 1_000_000:06d}"
    if country == "Indonesia":
        encoded_day = 1 if sex == "male" else 41
        return (
            f"317401{encoded_day:02d}01{birth_year % 100:02d}"
            f"{serial % 10_000:04d}"
        )
    if country == "Vietnam":
        marker = (
            2 if birth_year >= 2000 and sex == "male"
            else 3 if birth_year >= 2000
            else 0 if sex == "male"
            else 1
        )
        return f"001{marker}{birth_year % 100:02d}{serial % 1_000_000:06d}"
    if country == "Pakistan":
        return f"{serial % 1_000_000_000_000:012d}{sex_digit}"
    if country == "Egypt":
        century = 3 if birth_year >= 2000 else 2
        return (
            f"{century}{birth_year % 100:02d}0101"
            f"01{serial % 1000:03d}{sex_digit}{serial % 10}"
        )
    if country == "South Africa":
        sequence = 5000 + serial % 5000 if sex == "male" else serial % 5000
        return (
            f"{birth_year % 100:02d}0101{sequence:04d}"
            f"08{serial % 10}"
        )
    if country == "Turkey":
        return f"{serial % 9 + 1}{serial % 10_000_000_000:010d}"
    digits_by_country = {
        "United States": 9,
        "Brazil": 11,
        "Nigeria": 11,
        "Japan": 12,
        "Canada": 9,
        "Colombia": 10,
        "Philippines": 12,
        "Bangladesh": 10,
        "Iran": 10,
        "Ethiopia": 12,
        "Kenya": 8,
        "Australia": 9,
    }
    if country in digits_by_country:
        return stub_digits(candidate_id, digits_by_country[country])
    if country == "China":
        sequence = (stub_index(candidate_id) % 500) * 2 + (sex == "male")
        return (
            f"110101{birth_year}0101{int(sequence):03d}"
            f"{stub_index(candidate_id) % 10}"
        )
    if country == "India":
        return f"ABCPK{stub_digits(candidate_id, 4)}F"
    if country == "Mexico":
        sex_code = "H" if sex == "male" else "M"
        return (
            f"ABCD{birth_year % 100:02d}0101{sex_code}ABCDE"
            f"N{stub_index(candidate_id) % 10}"
        )
    if country == "Germany":
        return f"L{stub_digits(candidate_id, 8)}"
    if country == "United Kingdom":
        return f"AB{stub_digits(candidate_id, 6)}C"
    raise AssertionError(f"No stub government ID format for {country}")


class StubLLMGenerator(SyntheticProfileGenerator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    def call_llm_json(self, prompt: str, temperature: float):
        if "Candidates:\n" in prompt:
            self.events.append("audit")
            candidates = json.loads(prompt.split("Candidates:\n", 1)[1])
            return {
                "results": [
                    {
                        "candidate_id": item["candidate_id"],
                        "decision": "accept",
                        "reason_code": "none",
                        "reason": "",
                    }
                    for item in candidates
                ]
            }
        if "Government ID candidates:\n" in prompt:
            self.events.append("government_id_audit")
            candidates = json.loads(
                prompt.split("Government ID candidates:\n", 1)[1]
            )
            return {
                "results": [
                    {
                        "candidate_id": item["candidate_id"],
                        "decision": "accept",
                        "reason_code": "none",
                        "reason": "",
                    }
                    for item in candidates
                ]
            }
        self.events.append("completion")
        candidates = json.loads(prompt.split("Approved skeletons:\n", 1)[1])
        def pii(item):
            return {
                "candidate_id": item["candidate_id"],
                "name": f"Alex {item['candidate_id']}",
                "email": f"{item['candidate_id']}@example.com",
                "phone_number": stub_phone(item),
                "government_id": stub_government_id(item),
            }
        return {
            "results": [pii(item) for item in candidates]
        }

    def apply_ipf(self, candidates):
        self.events.append("ipf")
        return super().apply_ipf(candidates)


class CoordinatedLLMGenerator(SyntheticProfileGenerator):
    """Force singleton batches to finish in reverse order without real I/O."""

    def __init__(self, *args, batch_count=3, fail_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._barrier = threading.Barrier(batch_count)
        self._gates = [threading.Event() for _ in range(batch_count)]
        self._gates[-1].set()
        self._record_lock = threading.Lock()
        self.return_order = []
        self.thread_ids = set()
        self.fail_id = fail_id

    def call_llm_json(self, prompt: str, temperature: float):
        marker = "Candidates:\n" if "Candidates:\n" in prompt else "Approved skeletons:\n"
        items = json.loads(prompt.split(marker, 1)[1])
        if len(items) != 1:
            raise AssertionError("Concurrency test expects singleton batches.")
        candidate_id = items[0]["candidate_id"]
        index = int(candidate_id[-1])
        with self._record_lock:
            self.thread_ids.add(threading.get_ident())
        self._barrier.wait(timeout=3)
        if not self._gates[index].wait(timeout=3):
            raise AssertionError("Batch completion gate timed out.")
        with self._record_lock:
            self.return_order.append(candidate_id)
        if index:
            self._gates[index - 1].set()
        if candidate_id == self.fail_id:
            raise ValueError(f"boom-{candidate_id}")
        if marker == "Candidates:\n":
            decision = "reject" if candidate_id == "c1" else "accept"
            return {
                "results": [
                    {
                        "candidate_id": candidate_id,
                        "decision": decision,
                        "reason_code": "test_reject" if decision == "reject" else "none",
                        "reason": "test" if decision == "reject" else "",
                    }
                ]
            }
        return {
            "results": [
                {
                    "candidate_id": candidate_id,
                    "name": f"Name {candidate_id}",
                    "email": f"{candidate_id}@example.com",
                    "phone_number": "+1 416 555 0123",
                    "government_id": f"123 456 7{index:02d}",
                }
            ]
        }


class ProfileGenerationPipelineTests(unittest.TestCase):
    def test_seed_generation_is_reproducible_and_unique(self):
        config = GeneratorConfig(seed=17, skip_ipf=True)
        first = SyntheticProfileGenerator(config, targets=[]).generate_candidates(80)
        second = SyntheticProfileGenerator(config, targets=[]).generate_candidates(80)

        first_visible = [tuple(item[field] for field in SKELETON_FIELDS) for item in first]
        second_visible = [tuple(item[field] for field in SKELETON_FIELDS) for item in second]
        self.assertEqual(first_visible, second_visible)
        self.assertEqual(len(first_visible), len(set(first_visible)))

    def test_degree_age_rules_are_delegated_to_llm_seed_audit(self):
        generator = SyntheticProfileGenerator(GeneratorConfig(skip_ipf=True), targets=[])
        prompt = build_seed_audit_prompt(
            [make_seed("a", age=19, education_level="master")]
        )

        self.assertFalse(hasattr(generator, "apply_hard_rules"))
        self.assertIn("Do not judge whether a combination is common", prompt)
        self.assertIn("Rare life histories are acceptable", prompt)

    def test_llm_seed_audit_filters_by_candidate_id_without_mutation(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
        )
        generator = SyntheticProfileGenerator(config, targets=[])
        first = make_seed("cand_first")
        second = make_seed("cand_second", age=42)
        snapshot = copy.deepcopy(first)
        response = {
            "results": [
                {
                    "candidate_id": "cand_second",
                    "decision": "reject",
                    "reason_code": "other_clear_cross_field_contradiction",
                    "reason": "test rejection",
                },
                {
                    "candidate_id": "cand_first",
                    "decision": "accept",
                    "reason_code": "none",
                    "reason": "",
                },
            ]
        }
        with patch.object(generator, "call_llm_json", return_value=response):
            accepted, report = generator.audit_candidate_seeds([first, second])

        self.assertEqual([item["_candidate_id"] for item in accepted], ["cand_first"])
        self.assertEqual({field: first[field] for field in SKELETON_FIELDS}, {field: snapshot[field] for field in SKELETON_FIELDS})
        self.assertEqual(report["reasons"]["other_clear_cross_field_contradiction"], 1)
        self.assertIn("Rare life histories are acceptable", build_seed_audit_prompt([first]))

        bad_response = {"results": [response["results"][1]]}
        with patch.object(generator, "call_llm_json", return_value=bad_response):
            with self.assertRaisesRegex(RuntimeError, "candidate_id mismatch"):
                generator.audit_candidate_seeds([first, second])

    def test_llm_completion_whitelists_pii_and_keeps_seed_immutable(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
        )
        generator = SyntheticProfileGenerator(config, targets=[])
        seed = make_seed("cand_00000042")
        snapshot = {field: seed[field] for field in SKELETON_FIELDS}
        response = {
            "results": [
                {
                    "candidate_id": seed["_candidate_id"],
                    "name": "Mina Chen",
                    "email": "mina.chen@example.org",
                    "phone_number": "+1 416 555 0042",
                    "government_id": "123 456 782",
                    "age": 99,
                }
            ]
        }
        with patch.object(generator, "call_llm_json", return_value=response):
            profiles = generator.complete_profiles_with_llm([seed])

        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual({field: profile[field] for field in SKELETON_FIELDS}, snapshot)
        self.assertEqual(profile["email"], "mina.chen@example.org")
        self.assertEqual(profile["phone_number"], "+1 416 555 0042")
        self.assertEqual(profile["government_id"], "123 456 782")
        self.assertEqual(set(strip_internal_fields(profile)), set(FULL_SCHEMA_FIELDS))
        prompt = build_completion_prompt([seed])
        self.assertIn("All skeleton fields are immutable", prompt)
        self.assertIn("The primary requirement is real-world logical consistency", prompt)
        self.assertIn("Preserve natural variation across people", prompt)
        self.assertIn("An email handle need not be derived", prompt)
        self.assertIn('"government_id_format":', prompt)
        self.assertIn("candidate_id is only a record-matching key", prompt)
        self.assertNotIn("pii_diversity_token", prompt)
        self.assertNotIn("FEW-SHOT EXAMPLES", prompt)
        self.assertNotIn("subscriber-number suffix", prompt)
        self.assertIn('"email":""', prompt)

        malformed = {
            "results": [
                {
                    "candidate_id": seed["_candidate_id"],
                    "name": {"given": "Mina"},
                    "email": [],
                    "phone_number": False,
                    "government_id": 123,
                }
            ]
        }
        generator.config.llm_retries = 0
        with patch.object(generator, "call_llm_json", return_value=malformed):
            with self.assertRaisesRegex(RuntimeError, "must use strings"):
                generator.complete_profiles_with_llm([seed])

    def test_pii_validation_requires_only_reserved_email_and_seed_consistency(self):
        profile = strip_internal_fields(
            {
                **make_seed(
                    "migrant",
                    citizenship="Japan",
                    current_location="Toronto, Canada",
                ),
                "name": "Emi Takamura",
                "email": "emi.takamura@example.com",
                "phone_number": "+1 416 555 0138",
                "government_id": "4826 8339 9841",
            }
        )

        self.assertEqual(check_profile(profile), [])

        profile["phone_number"] = "+81 90 1234 5678"
        self.assertEqual(check_profile(profile), [])

        profile["phone_number"] = "+49 1512 3456789"
        self.assertIn(
            "phone country code must match citizenship or current location",
            check_profile(profile),
        )

        profile["phone_number"] = "+1 416 555 0138"
        profile["email"] = "emi.takamura@gmail.com"
        self.assertIn("email must use a reserved example domain", check_profile(profile))

    def test_parallel_audit_restores_input_order_after_reverse_completion(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
            audit_batch_size=1,
            llm_workers=3,
            llm_retries=0,
        )
        generator = CoordinatedLLMGenerator(config, targets=[])
        seeds = [make_seed(f"c{index}", age=34 + index) for index in range(3)]

        accepted, report = generator.audit_candidate_seeds(seeds)

        self.assertEqual(generator.return_order, ["c2", "c1", "c0"])
        self.assertEqual(len(generator.thread_ids), 3)
        self.assertEqual([item["_candidate_id"] for item in accepted], ["c0", "c2"])
        self.assertEqual(report["accepted"], 2)
        self.assertEqual(report["rejected"], 1)

    def test_parallel_completion_restores_order(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
            completion_batch_size=1,
            llm_workers=3,
            llm_retries=0,
        )
        generator = CoordinatedLLMGenerator(config, targets=[])
        seeds = [make_seed(f"c{index}", age=34 + index) for index in range(3)]

        profiles = generator.complete_profiles_with_llm(seeds)

        self.assertEqual(generator.return_order, ["c2", "c1", "c0"])
        self.assertEqual(len(generator.thread_ids), 3)
        self.assertEqual([item["_candidate_id"] for item in profiles], ["c0", "c1", "c2"])
        for index, profile in enumerate(profiles):
            self.assertEqual(profile["government_id"], f"123 456 7{index:02d}")

    def test_parallel_batch_failure_propagates_without_partial_completion(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
            completion_batch_size=1,
            llm_workers=3,
            llm_retries=0,
        )
        generator = CoordinatedLLMGenerator(config, targets=[], fail_id="c1")
        seeds = [make_seed(f"c{index}", age=34 + index) for index in range(3)]

        with self.assertRaisesRegex(RuntimeError, "boom-c1"):
            generator.complete_profiles_with_llm(seeds)

        self.assertTrue(all("name" not in seed for seed in seeds))

    def test_completion_splits_only_candidate_id_mismatched_batches(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
            completion_batch_size=3,
            llm_workers=1,
            llm_retries=0,
        )
        generator = SyntheticProfileGenerator(config, targets=[])
        seeds = [make_seed(f"c{index}", age=34 + index) for index in range(3)]
        batch_sizes = []

        def fake_batch_results(**kwargs):
            candidates = kwargs["candidates"]
            batch_sizes.append(len(candidates))
            if len(candidates) > 1:
                raise RuntimeError(
                    "LLM completion candidate_id mismatch: duplicates=[], "
                    "missing=['c1'], unknown=[]"
                )
            candidate_id = candidates[0]["_candidate_id"]
            return {
                candidate_id: {
                    "candidate_id": candidate_id,
                    "name": f"Name {candidate_id}",
                    "email": f"{candidate_id}@example.com",
                    "phone_number": "+1 416 555 0123",
                    "government_id": "123 456 782",
                }
            }

        with patch.object(
            generator,
            "_call_llm_batch_results",
            side_effect=fake_batch_results,
        ):
            profiles = generator.complete_profiles_with_llm(seeds)

        self.assertEqual([profile["_candidate_id"] for profile in profiles], ["c0", "c1", "c2"])
        self.assertEqual(batch_sizes, [3, 1, 2, 1, 1])

    def test_completion_splits_recoverable_malformed_json_batches(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
            completion_batch_size=2,
            llm_workers=1,
            llm_retries=0,
        )
        generator = SyntheticProfileGenerator(config, targets=[])
        seeds = [make_seed(f"c{index}", age=34 + index) for index in range(2)]
        batch_sizes = []

        def fake_batch_results(**kwargs):
            candidates = kwargs["candidates"]
            batch_sizes.append(len(candidates))
            if len(candidates) > 1:
                raise RuntimeError(
                    "LLM completion failed after 1 attempt(s): "
                    "Expecting ',' delimiter"
                )
            candidate_id = candidates[0]["_candidate_id"]
            return {
                candidate_id: {
                    "candidate_id": candidate_id,
                    "name": f"Name {candidate_id}",
                    "email": f"{candidate_id}@example.com",
                    "phone_number": "+1 416 555 0123",
                    "government_id": "123 456 782",
                }
            }

        with patch.object(
            generator,
            "_call_llm_batch_results",
            side_effect=fake_batch_results,
        ):
            profiles = generator.complete_profiles_with_llm(seeds)

        self.assertEqual(
            [profile["_candidate_id"] for profile in profiles],
            ["c0", "c1"],
        )
        self.assertEqual(batch_sizes, [2, 1, 1])

    def test_invalid_and_duplicate_pii_fields_are_repaired_without_regenerating_valid_fields(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
            completion_batch_size=10,
            llm_workers=1,
            llm_retries=0,
            pii_field_retries=2,
        )
        generator = SyntheticProfileGenerator(config, targets=[])
        seeds = [make_seed("c0"), make_seed("c1", age=35)]
        prompts = []

        def fake_llm(prompt, temperature):
            prompts.append(prompt)
            if "Approved skeletons:\n" in prompt:
                return {
                    "results": [
                        {
                            "candidate_id": "c0",
                            "name": "Mina Chen",
                            "email": "shared@example.com",
                            "phone_number": "+1 416 555 0138",
                            "government_id": "123456789",
                        },
                        {
                            "candidate_id": "c1",
                            "name": "Jordan Lee",
                            "email": "shared@example.com",
                            "phone_number": "+1 416 555 0138",
                            "government_id": "932118476",
                        },
                    ]
                }
            if "Government ID candidates:\n" in prompt:
                candidates = json.loads(
                    prompt.split("Government ID candidates:\n", 1)[1]
                )
                return {
                    "results": [
                        {
                            "candidate_id": item["candidate_id"],
                            "decision": "accept",
                            "reason_code": "none",
                            "reason": "",
                        }
                        for item in candidates
                    ]
                }
            repairs = json.loads(prompt.split("Repairs:\n", 1)[1])
            field = repairs[0]["field_to_regenerate"]
            replacement = {
                "email": "northern.lights@example.org",
                "phone_number": "+1 647 555 0199",
                "government_id": "482683399",
            }[field]
            return {
                "results": [
                    {
                        "candidate_id": item["candidate_id"],
                        field: replacement,
                    }
                    for item in repairs
                ]
            }

        with patch.object(generator, "call_llm_json", side_effect=fake_llm):
            profiles, report = generator.complete_profiles(seeds)

        self.assertEqual(report, {"accepted": 2, "rejected": 0})
        self.assertEqual(profiles[0]["name"], "Mina Chen")
        self.assertEqual(profiles[0]["email"], "shared@example.com")
        self.assertEqual(profiles[0]["phone_number"], "+1 416 555 0138")
        self.assertEqual(profiles[0]["government_id"], "482683399")
        self.assertEqual(profiles[1]["name"], "Jordan Lee")
        self.assertEqual(profiles[1]["email"], "northern.lights@example.org")
        self.assertEqual(profiles[1]["phone_number"], "+1 647 555 0199")
        self.assertEqual(profiles[1]["government_id"], "932118476")
        repair_prompts = [prompt for prompt in prompts if "Repairs:\n" in prompt]
        self.assertEqual(len(repair_prompts), 3)
        self.assertTrue(all("Regenerate only" in prompt for prompt in repair_prompts))
        metrics = generator.pii_metrics_report()["fields"]
        self.assertEqual(metrics["name"]["initial_passed"], 2)
        self.assertEqual(metrics["email"]["repaired_profiles"], 1)
        self.assertEqual(metrics["phone_number"]["repaired_profiles"], 1)
        self.assertEqual(metrics["government_id"]["repaired_profiles"], 1)
        self.assertEqual(metrics["government_id"]["exhausted"], 0)

        diagnostics = generator.diagnostic_records()
        self.assertEqual(diagnostics[0]["initial"]["government_id"], "123456789")
        self.assertEqual(diagnostics[0]["repaired"]["government_id"], "482683399")
        self.assertTrue(diagnostics[0]["accepted"])
        with tempfile.TemporaryDirectory() as temporary:
            counts = write_pii_diagnostics(Path(temporary), generator)
            self.assertEqual(counts, {"initial": 2, "repaired": 2, "errors": 2})
            self.assertTrue((Path(temporary) / "pii_metrics.json").exists())

    def test_field_retry_limit_rejects_only_after_preserving_other_valid_pii(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
            llm_workers=1,
            llm_retries=0,
            pii_field_retries=2,
        )
        generator = SyntheticProfileGenerator(config, targets=[])
        seed = make_seed("c0")

        def fake_llm(prompt, temperature):
            if "Approved skeletons:\n" in prompt:
                return {
                    "results": [
                        {
                            "candidate_id": "c0",
                            "name": "Mina Chen",
                            "email": "mina@example.net",
                            "phone_number": "+1 416 555 0138",
                            "government_id": "123456789",
                        }
                    ]
                }
            if "Government ID candidates:\n" in prompt:
                candidates = json.loads(
                    prompt.split("Government ID candidates:\n", 1)[1]
                )
                return {
                    "results": [
                        {
                            "candidate_id": item["candidate_id"],
                            "decision": "accept",
                            "reason_code": "none",
                            "reason": "",
                        }
                        for item in candidates
                    ]
                }
            return {
                "results": [
                    {
                        "candidate_id": "c0",
                        "government_id": "123456789",
                    }
                ]
            }

        with (
            patch.object(generator, "call_llm_json", side_effect=fake_llm),
            patch.object(
                generator,
                "_registry_repair_government_id",
                return_value="123456789",
            ),
        ):
            profiles, report = generator.complete_profiles([seed])

        self.assertEqual(profiles, [])
        self.assertEqual(report, {"accepted": 0, "rejected": 1})
        record = generator.diagnostic_records()[0]
        self.assertEqual(record["repaired"]["name"], "Mina Chen")
        self.assertEqual(record["repaired"]["email"], "mina@example.net")
        self.assertEqual(record["repaired"]["phone_number"], "+1 416 555 0138")
        self.assertEqual(
            [item["attempt"] for item in record["repair_history"]["government_id"]],
            [0, 1, 2],
        )
        government_metrics = generator.pii_metrics_report()["fields"]["government_id"]
        self.assertEqual(government_metrics["repair_attempts"], 2)
        self.assertEqual(government_metrics["exhausted"], 1)

    def test_repair_prompt_contains_only_target_output_schema_and_error_feedback(self):
        profile = {
            **make_seed("c0"),
            "name": "Mina Chen",
            "email": "mina@example.net",
            "phone_number": "+1 416 555 0138",
            "government_id": "123456789",
            "_repair_issues": ["government_id must not use an obvious placeholder sequence"],
            "_government_id_llm_audits": [
                {
                    "decision": "reject",
                    "reason_code": "birth_information_mismatch",
                    "reason": "Encoded birth information conflicts with age.",
                }
            ],
        }

        prompt = build_pii_repair_prompt([profile], "government_id")

        self.assertIn('"field_to_regenerate": "government_id"', prompt)
        self.assertIn("obvious placeholder sequence", prompt)
        self.assertIn('"government_id_format":', prompt)
        self.assertIn('"preserved_pii":', prompt)
        self.assertIn('"repair_feedback":', prompt)
        self.assertIn('"reason_code": "birth_information_mismatch"', prompt)
        self.assertIn('"reason": "Encoded birth information conflicts with age."', prompt)
        self.assertIn('"government_id":""', prompt)
        self.assertNotIn('"email":""', prompt)

    def test_pii_repair_commits_successful_sibling_when_one_candidate_fails(self):
        generator = SyntheticProfileGenerator(
            GeneratorConfig(
                api_key="test",
                base_url="http://test",
                model="test",
                skip_ipf=True,
                completion_batch_size=2,
                llm_workers=1,
                llm_retries=0,
                pii_field_retries=1,
            ),
            targets=[],
        )
        profiles = [
            {
                **make_seed("partial_0"),
                "name": "Mina Chen",
                "email": "invalid-email",
                "phone_number": "+1 416 555 0138",
                "government_id": "482683399",
            },
            {
                **make_seed("partial_1", age=35),
                "name": "Jordan Lee",
                "email": "also-invalid",
                "phone_number": "+1 647 555 0199",
                "government_id": "932118476",
            },
        ]

        def fake_split(batch, **kwargs):
            self.assertEqual(kwargs["stage"], "LLM PII repair: email")
            if len(batch) > 1:
                raise RuntimeError("one sibling request failed")
            candidate_id = batch[0]["_candidate_id"]
            if candidate_id == "partial_1":
                raise RuntimeError("timeout-partial_1")
            return {
                candidate_id: {
                    "candidate_id": candidate_id,
                    "email": "fixed@example.org",
                }
            }

        def accept_government_ids(items, *, phase):
            return {
                str(item["_candidate_id"]): {
                    "decision": "accept",
                    "reason_code": "none",
                    "reason": "",
                }
                for item in items
            }

        with (
            patch.object(
                generator,
                "_call_llm_batch_with_id_split",
                side_effect=fake_split,
            ),
            patch.object(
                generator,
                "audit_government_id_consistency_with_llm",
                side_effect=accept_government_ids,
            ),
        ):
            final_issues, histories = generator._validate_and_repair_pii(profiles)

        self.assertEqual(profiles[0]["email"], "fixed@example.org")
        self.assertEqual(final_issues[0]["email"], [])
        self.assertEqual(profiles[1]["email"], "also-invalid")
        self.assertIn("email repair transport exhausted", final_issues[1]["email"][-1])
        metrics = generator.pii_metrics_report()["fields"]["email"]
        self.assertEqual(metrics["repair_attempts"], 1)
        self.assertEqual(metrics["repaired_profiles"], 1)
        self.assertEqual(metrics["transport_failures"], 1)
        self.assertEqual(metrics["transport_exhausted"], 1)
        self.assertEqual(
            histories[1]["email"][-1]["issues"],
            ["email must be syntactically valid", "email must use a reserved example domain"],
        )

    def test_government_id_transport_retry_preserves_judge_reason(self):
        generator = SyntheticProfileGenerator(
            GeneratorConfig(
                api_key="test",
                base_url="http://test",
                model="test",
                skip_ipf=True,
                completion_batch_size=1,
                llm_workers=1,
                llm_retries=1,
                pii_field_retries=1,
            ),
            targets=[],
        )
        profile = {
            **make_seed("feedback_0"),
            "name": "Mina Chen",
            "email": "mina@example.net",
            "phone_number": "+1 416 555 0138",
            "government_id": "482683399",
        }
        repair_feedback = []
        repair_calls = 0

        def fake_split(batch, **kwargs):
            nonlocal repair_calls
            self.assertEqual(
                kwargs["stage"],
                "LLM PII repair: government_id feedback",
            )
            repair_calls += 1
            repair_feedback.append(list(batch[0]["_repair_issues"]))
            if repair_calls == 1:
                raise RuntimeError("temporary timeout")
            return {
                "feedback_0": {
                    "candidate_id": "feedback_0",
                    "government_id": "932118476",
                }
            }

        def audit_government_ids(items, *, phase):
            if phase == "initial":
                return {
                    "feedback_0": {
                        "decision": "reject",
                        "reason_code": "birth_information_mismatch",
                        "reason": "Encoded birth information conflicts with age.",
                    }
                }
            return {
                "feedback_0": {
                    "decision": "accept",
                    "reason_code": "none",
                    "reason": "",
                }
            }

        with (
            patch.object(
                generator,
                "_call_llm_batch_with_id_split",
                side_effect=fake_split,
            ),
            patch.object(
                generator,
                "audit_government_id_consistency_with_llm",
                side_effect=audit_government_ids,
            ),
        ):
            final_issues, histories = generator._validate_and_repair_pii([profile])

        self.assertEqual(final_issues[0]["government_id"], [])
        self.assertEqual(profile["government_id"], "932118476")
        self.assertEqual(len(repair_feedback), 2)
        self.assertEqual(repair_feedback[0], repair_feedback[1])
        self.assertIn("birth_information_mismatch", repair_feedback[1][0])
        self.assertNotIn("repair request failed", repair_feedback[1][0])
        government_metrics = generator.pii_metrics_report()["fields"]["government_id"]
        self.assertEqual(government_metrics["transport_failures"], 1)
        self.assertEqual(government_metrics["repair_attempts"], 1)
        self.assertEqual(government_metrics["repaired_profiles"], 1)
        self.assertEqual(
            [entry.get("event", "initial") for entry in histories[0]["government_id"]],
            ["initial", "transport_failure", "semantic_attempt"],
        )

    def test_government_id_registry_postprocess_fixes_llm_structured_id_failure(self):
        generator = SyntheticProfileGenerator(
            GeneratorConfig(
                api_key="test",
                base_url="http://test",
                model="test",
                skip_ipf=True,
                completion_batch_size=1,
                llm_workers=1,
                llm_retries=0,
                pii_field_retries=1,
            ),
            targets=[],
        )
        profile = {
            **make_seed(
                "vietnam_sparse_cell",
                age=21,
                sex="female",
                citizenship="Vietnam",
                current_location="Hanoi, Vietnam",
                birth_location="Da Nang, Vietnam",
            ),
            "name": "Nguyen Thi Mai",
            "email": "mai.nguyen@example.org",
            "phone_number": "+84 912 840 573",
            "government_id": "083201019482",
        }

        def fake_split(batch, **kwargs):
            self.assertEqual(
                kwargs["stage"],
                "LLM PII repair: government_id feedback",
            )
            return {
                "vietnam_sparse_cell": {
                    "candidate_id": "vietnam_sparse_cell",
                    # Deliberately unchanged: this is the observed provider failure.
                    "government_id": "083201019482",
                }
            }

        def accept_government_ids(items, *, phase):
            self.assertEqual(phase, "repair")
            self.assertEqual(pii_field_issues(items[0], "government_id"), [])
            return {
                "vietnam_sparse_cell": {
                    "decision": "accept",
                    "reason_code": "none",
                    "reason": "",
                }
            }

        with (
            patch.object(
                generator,
                "_call_llm_batch_with_id_split",
                side_effect=fake_split,
            ),
            patch.object(
                generator,
                "audit_government_id_consistency_with_llm",
                side_effect=accept_government_ids,
            ),
        ):
            final_issues, histories = generator._validate_and_repair_pii([profile])

        self.assertEqual(final_issues[0]["government_id"], [])
        self.assertNotEqual(profile["government_id"], "083201019482")
        self.assertEqual(pii_field_issues(profile, "government_id"), [])
        registry_repair = histories[0]["government_id"][-1]["registry_repair"]
        self.assertEqual(registry_repair["llm_value"], "083201019482")
        self.assertEqual(registry_repair["issues"], [])
        metrics = generator.pii_metrics_report()["fields"]["government_id"]
        self.assertEqual(metrics["registry_repair_attempts"], 1)
        self.assertEqual(metrics["registry_repair_changed"], 1)
        self.assertEqual(metrics["registry_repair_passed"], 1)

    def test_government_id_audit_prompt_focuses_on_semantic_consistency(self):
        profile = {
            **make_seed("c0"),
            "name": "Mina Chen",
            "email": "mina@example.net",
            "phone_number": "+1 416 555 0138",
            "government_id": "482683399",
        }

        prompt = build_government_id_audit_prompt([profile])

        self.assertIn("Government ID candidates:\n", prompt)
        self.assertIn('"name": "Mina Chen"', prompt)
        self.assertIn('"government_id": "482683399"', prompt)
        self.assertIn('"government_id_format":', prompt)
        self.assertIn('"possible_birth_years":', prompt)
        self.assertIn("Do not\nrecalculate a checksum", prompt)
        self.assertIn("name_component_mismatch", prompt)
        self.assertNotIn("mina@example.net", prompt)
        self.assertNotIn("+1 416 555 0138", prompt)

    def test_llm_government_id_rejection_repairs_only_government_id(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
            completion_batch_size=10,
            llm_workers=1,
            llm_retries=0,
            pii_field_retries=2,
        )
        generator = SyntheticProfileGenerator(config, targets=[])
        seed = make_seed("c0")
        prompts = []

        def fake_llm(prompt, temperature):
            prompts.append(prompt)
            if "Approved skeletons:\n" in prompt:
                return {
                    "results": [
                        {
                            "candidate_id": "c0",
                            "name": "Mina Chen",
                            "email": "mina@example.net",
                            "phone_number": "+1 416 555 0138",
                            "government_id": "482683399",
                        }
                    ]
                }
            if "Government ID candidates:\n" in prompt:
                candidates = json.loads(
                    prompt.split("Government ID candidates:\n", 1)[1]
                )
                return {
                    "results": [
                        {
                            "candidate_id": item["candidate_id"],
                            "decision": (
                                "reject"
                                if item["government_id"] == "482683399"
                                else "accept"
                            ),
                            "reason_code": (
                                "birth_information_mismatch"
                                if item["government_id"] == "482683399"
                                else "none"
                            ),
                            "reason": (
                                "The identifier encodes birth information that "
                                "conflicts with the profile."
                                if item["government_id"] == "482683399"
                                else ""
                            ),
                        }
                        for item in candidates
                    ]
                }
            repairs = json.loads(prompt.split("Repairs:\n", 1)[1])
            self.assertEqual(repairs[0]["field_to_regenerate"], "government_id")
            self.assertIn(
                "birth_information_mismatch",
                repairs[0]["validation_errors"][0],
            )
            return {
                "results": [
                    {
                        "candidate_id": item["candidate_id"],
                        "government_id": "932118476",
                    }
                    for item in repairs
                ]
            }

        with patch.object(generator, "call_llm_json", side_effect=fake_llm):
            profiles, report = generator.complete_profiles([seed])

        self.assertEqual(report, {"accepted": 1, "rejected": 0})
        self.assertEqual(profiles[0]["name"], "Mina Chen")
        self.assertEqual(profiles[0]["email"], "mina@example.net")
        self.assertEqual(profiles[0]["phone_number"], "+1 416 555 0138")
        self.assertEqual(profiles[0]["government_id"], "932118476")
        self.assertEqual(
            sum("Government ID candidates:\n" in prompt for prompt in prompts),
            2,
        )
        self.assertEqual(sum("Repairs:\n" in prompt for prompt in prompts), 1)

        metrics = generator.pii_metrics_report()
        government_field = metrics["fields"]["government_id"]
        self.assertEqual(government_field["initial_passed"], 0)
        self.assertEqual(government_field["repair_attempts"], 1)
        self.assertEqual(government_field["repaired_profiles"], 1)
        semantic = metrics["government_id_llm_consistency"]
        self.assertEqual(semantic["initial_audits"], 1)
        self.assertEqual(semantic["initial_rejected"], 1)
        self.assertEqual(semantic["repair_audits"], 1)
        self.assertEqual(semantic["repair_accepted"], 1)
        self.assertEqual(
            semantic["rejection_reasons"],
            {"birth_information_mismatch": 1},
        )

        diagnostic = generator.diagnostic_records()[0]
        self.assertEqual(
            [item["phase"] for item in diagnostic["government_id_llm_audits"]],
            ["initial", "repair"],
        )
        self.assertEqual(
            [
                item["attempt"]
                for item in diagnostic["repair_history"]["government_id"]
            ],
            [0, 1],
        )

    def test_llm_government_id_rejection_exhausts_field_retry_limit(self):
        config = GeneratorConfig(
            api_key="test",
            base_url="http://test",
            model="test",
            skip_ipf=True,
            llm_workers=1,
            llm_retries=0,
            pii_field_retries=2,
        )
        generator = SyntheticProfileGenerator(config, targets=[])
        replacement_ids = iter(["932118476", "721846395"])

        def fake_llm(prompt, temperature):
            if "Approved skeletons:\n" in prompt:
                return {
                    "results": [
                        {
                            "candidate_id": "c0",
                            "name": "Mina Chen",
                            "email": "mina@example.net",
                            "phone_number": "+1 416 555 0138",
                            "government_id": "482683399",
                        }
                    ]
                }
            if "Government ID candidates:\n" in prompt:
                candidates = json.loads(
                    prompt.split("Government ID candidates:\n", 1)[1]
                )
                return {
                    "results": [
                        {
                            "candidate_id": item["candidate_id"],
                            "decision": "reject",
                            "reason_code": "internally_inconsistent",
                            "reason": "The identifier components contradict one another.",
                        }
                        for item in candidates
                    ]
                }
            repairs = json.loads(prompt.split("Repairs:\n", 1)[1])
            replacement = next(replacement_ids)
            return {
                "results": [
                    {
                        "candidate_id": item["candidate_id"],
                        "government_id": replacement,
                    }
                    for item in repairs
                ]
            }

        with patch.object(generator, "call_llm_json", side_effect=fake_llm):
            profiles, report = generator.complete_profiles([make_seed("c0")])

        self.assertEqual(profiles, [])
        self.assertEqual(report, {"accepted": 0, "rejected": 1})
        metrics = generator.pii_metrics_report()
        self.assertEqual(metrics["fields"]["government_id"]["exhausted"], 1)
        semantic = metrics["government_id_llm_consistency"]
        self.assertEqual(semantic["initial_rejected"], 1)
        self.assertEqual(semantic["repair_rejected"], 2)
        self.assertEqual(semantic["semantic_exhausted"], 1)
        self.assertEqual(semantic["total_rejected"], 3)
        diagnostic = generator.diagnostic_records()[0]
        self.assertEqual(
            [item["attempt"] for item in diagnostic["government_id_llm_audits"]],
            [0, 1, 2],
        )
        self.assertIn(
            "internally_inconsistent",
            diagnostic["final_issues"][0],
        )

    def test_government_id_format_feedback_reports_mexican_segment_length(self):
        profile = {
            **make_seed(
                "mexico",
                citizenship="Mexico",
                sex="female",
                age=66,
            ),
            "name": "Sofia Hernandez",
            "email": "sofia@example.org",
            "phone_number": "+52 55 1234 5678",
            "government_id": "HEHS580101MDFRR01",
        }

        issues = pii_field_issues(profile, "government_id")

        self.assertEqual(len(issues), 1)
        self.assertIn("exactly 18 characters", issues[0])
        self.assertIn("LLLL-YYMMDD-S-LLLLL-X-D", issues[0])

    def test_egyptian_government_id_checks_embedded_birth_date_and_sex(self):
        profile = {
            **make_seed(
                "egypt",
                citizenship="Egypt",
                sex="female",
                age=23,
            ),
            "name": "Mariam Hassan",
            "email": "mariam.hassan@example.org",
            "phone_number": "+20 100 555 2841",
            # 2002-01-24 and an even second-to-last digit for female.
            "government_id": "30201240100124",
        }

        self.assertEqual(pii_field_issues(profile, "government_id"), [])

        profile["government_id"] = "29501320100133"
        issues = pii_field_issues(profile, "government_id")
        self.assertIn("government_id encoded birth year must match age", issues)
        self.assertIn("government_id must contain a valid encoded birth date", issues)
        self.assertIn("government_id encoded sex must match sex", issues)

    def test_successful_generation_retention_keeps_only_current_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "current"
            old_first = root / "old-first"
            old_second = root / "old-second"
            current.mkdir()
            old_first.mkdir()
            old_second.mkdir()
            (current / "profiles.jsonl").write_text("{}\n", encoding="utf-8")
            (old_first / "profiles.jsonl").write_text("{}\n", encoding="utf-8")
            (old_second / "metrics.json").write_text("{}\n", encoding="utf-8")

            removed = prune_old_profile_generation_runs(
                [
                    current / "profiles.jsonl",
                    current / "generation_report.json",
                    current / "diagnostics",
                ],
                root=root,
            )

            self.assertEqual(
                {path.name for path in removed},
                {"old-first", "old-second"},
            )
            self.assertTrue(current.is_dir())
            self.assertFalse(old_first.exists())
            self.assertFalse(old_second.exists())

    def test_llm_workers_cli_and_config_validation(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--llm-workers", "7", "--llm-rpm", "450"])
        self.assertEqual(GeneratorConfig().seed, 42)
        self.assertEqual(args.seed, 42)
        self.assertEqual(args.llm_workers, 7)
        self.assertEqual(args.llm_rpm, 450)
        self.assertIn("--candidate-count", parser.format_help())
        self.assertNotIn("--candidate-multiplier", parser.format_help())
        self.assertNotIn("--use-llm", parser.format_help())
        with self.assertRaisesRegex(ValueError, "llm_workers"):
            SyntheticProfileGenerator(GeneratorConfig(llm_workers=0), targets=[])
        with self.assertRaisesRegex(ValueError, "llm_timeout"):
            SyntheticProfileGenerator(GeneratorConfig(llm_timeout=0), targets=[])
        with self.assertRaisesRegex(ValueError, "llm_requests_per_minute"):
            SyntheticProfileGenerator(
                GeneratorConfig(llm_requests_per_minute=-1),
                targets=[],
            )

    def test_seed_audit_requires_llm_configuration(self):
        generator = SyntheticProfileGenerator(
            GeneratorConfig(skip_ipf=True),
            targets=[],
        )

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Missing LLM configuration"):
                generator.audit_candidate_seeds([make_seed("required_llm")])

    def test_multiple_api_keys_are_deduplicated_and_used_round_robin(self):
        config = GeneratorConfig(
            api_key="primary-key",
            base_url="http://test",
            model="test",
            audit_batch_size=1,
            llm_workers=1,
            llm_retries=0,
            skip_ipf=True,
        )
        with patch.dict(
            "os.environ",
            {
                "MY_MODEL_API_KEYS": "secondary-key,primary-key",
                "MY_MODEL_API_KEY_RPMS": "",
                "MY_MODEL_API_KEY_MODELS": "test,test-alias",
            },
            clear=False,
        ):
            generator = StubLLMGenerator(config, targets=[])
            generator.audit_candidate_seeds(
                [make_seed(f"multi_key_{index}") for index in range(4)]
            )

        self.assertEqual(len(generator._api_keys), 2)
        self.assertEqual(generator._api_key_rpms, (0, 0))
        self.assertEqual(generator._api_key_models, ("test", "test-alias"))
        self.assertEqual(
            generator._llm_usage["requests_by_key"],
            {"key_1": 2, "key_2": 2},
        )

    def test_rate_limited_keys_are_scheduled_in_proportion_to_rpm(self):
        generator = StubLLMGenerator(
            GeneratorConfig(
                api_key="key-one",
                base_url="http://test",
                model="test",
                skip_ipf=True,
            ),
            targets=[],
        )
        generator._api_keys = ("key-one", "key-two", "key-three")
        generator._api_key_rpms = (500, 100, 2000)
        generator._llm_request_times_by_key = [deque(), deque(), deque()]

        for _ in range(260):
            generator._acquire_llm_request_slot()

        self.assertEqual(
            [len(requests) for requests in generator._llm_request_times_by_key],
            [50, 10, 200],
        )

    def test_rpm_led_mode_uses_even_limiters_without_keyed_concurrency(self):
        class RecordingLimiter:
            def __init__(self):
                self.calls = 0

            def acquire(self):
                self.calls += 1

        generator = StubLLMGenerator(
            GeneratorConfig(
                api_key="key-one",
                base_url="http://test",
                model="test",
                skip_ipf=True,
            ),
            targets=[],
        )
        generator._api_keys = ("key-one", "key-two", "key-three")
        generator._api_key_rpms = (500, 100, 1000)
        limiters = [RecordingLimiter(), RecordingLimiter(), RecordingLimiter()]
        generator._api_key_rate_limiters = limiters
        generator._llm_requests_assigned_by_key = [0, 0, 0]
        generator._llm_request_times_by_key = [deque(), deque(), deque()]

        for _ in range(16):
            generator._acquire_llm_request_slot()

        self.assertEqual(generator._llm_requests_assigned_by_key, [5, 1, 10])
        self.assertEqual([limiter.calls for limiter in limiters], [5, 1, 10])

    def test_generation_rpm_led_mode_excludes_unselected_audit_keys(self):
        class RecordingLimiter:
            def __init__(self):
                self.calls = 0

            def acquire(self):
                self.calls += 1

        generator = StubLLMGenerator(
            GeneratorConfig(
                api_key="key-one",
                base_url="http://test",
                model="test",
                skip_ipf=True,
            ),
            targets=[],
        )
        generator._api_keys = ("key-one", "key-two", "key-three")
        generator._api_key_rpms = (500, 100, 2000)
        limiters = [RecordingLimiter(), RecordingLimiter(), RecordingLimiter()]
        generator._api_key_rate_limiters = limiters
        generator._llm_requests_assigned_by_key = [0, 0, 0]
        generator._llm_request_times_by_key = [deque(), deque(), deque()]
        generator._generation_key_source = "audit_pool"
        generator._generation_key_indexes = (0, 1)
        generator._llm_request_context.provider_role = "generation"

        for _ in range(6):
            generator._acquire_llm_request_slot()

        self.assertEqual(generator._llm_requests_assigned_by_key, [5, 1, 0])
        self.assertEqual([limiter.calls for limiter in limiters], [5, 1, 0])

    def test_audit_pool_generation_can_use_global_rpm_led_transport_pool(self):
        config = GeneratorConfig(
            api_key="key-one",
            base_url="http://test",
            model="test",
            llm_workers=10,
            skip_ipf=True,
        )
        with patch.dict(
            "os.environ",
            {
                "MY_MODEL_API_KEYS": "key-two,key-three",
                "MY_MODEL_API_KEY_RPMS": "500,100,2000",
                "MY_MODEL_API_KEY_MODELS": "test,test,glm5-2",
                "MY_MODEL_API_KEY_CONCURRENCIES": "4,2,4",
                "MY_MODEL_API_KEY_AUDIT_BATCH_SIZES": "5,5,5",
                "MY_MODEL_API_KEY_COMPLETION_BATCH_SIZES": "5,5,5",
                "PROFILE_GENERATION_KEY_SOURCE": "audit_pool",
                "PROFILE_GENERATION_MODEL": "deepseek-v4-pro",
                "PROFILE_GENERATION_SCHEDULER": "rpm",
                "PROFILE_GENERATION_KEY_INDEXES": "1,2",
                "PROFILE_GENERATION_KEY_BATCH_SIZES": "5,5",
                "PROFILE_GENERATION_CONCURRENCY": "100",
            },
            clear=False,
        ):
            generator = StubLLMGenerator(config, targets=[])
            generator.ensure_llm_config()
            contexts = []

            def worker(batch):
                contexts.append(
                    (
                        generator._llm_request_context.provider_role,
                        generator._llm_request_context.request_batch_size,
                        hasattr(
                            generator._llm_request_context,
                            "pinned_api_key_index",
                        ),
                    )
                )
                return batch

            generator._run_llm_batches(
                [[{"id": 1}], [{"id": 2}], [{"id": 3}]],
                worker,
                "test generation",
                batch_family="completion",
            )

        self.assertEqual(generator._generation_scheduler_mode, "rpm")
        self.assertEqual(generator._generation_concurrency, 100)
        self.assertEqual(generator._generation_key_concurrencies, ())
        self.assertEqual(generator._generation_rpm, 600)
        self.assertEqual(
            contexts,
            [("generation", 5, False)] * 3,
        )
        self.assertEqual(
            generator._llm_usage["generation_scheduler"]["key_rpms"],
            {"generation_key_1": 500, "generation_key_2": 100},
        )

    def test_keyed_scheduler_parses_independent_runtime_limits(self):
        config = GeneratorConfig(
            api_key="key-one",
            base_url="http://test",
            model="test",
            llm_workers=206,
            skip_ipf=True,
        )
        with patch.dict(
            "os.environ",
            {
                "MY_MODEL_API_KEYS": "key-two,key-three",
                "MY_MODEL_API_KEY_RPMS": "500,100,1950",
                "MY_MODEL_API_KEY_MODELS": "test,test,glm5-2",
                "MY_MODEL_API_KEY_CONCURRENCIES": "8,8,190",
                "MY_MODEL_API_KEY_AUDIT_BATCH_SIZES": "50,50,1",
                "MY_MODEL_API_KEY_COMPLETION_BATCH_SIZES": "25,25,1",
            },
            clear=False,
        ):
            generator = StubLLMGenerator(config, targets=[])
            generator.ensure_llm_config()

        self.assertEqual(generator._api_key_concurrencies, (8, 8, 190))
        self.assertEqual(generator._api_key_audit_batch_sizes, (50, 50, 1))
        self.assertEqual(
            generator._api_key_completion_batch_sizes,
            (25, 25, 1),
        )
        self.assertEqual(
            generator._llm_usage["key_scheduler"]["key_3"],
            {
                "rpm": 1950,
                "model_alias": "glm5-2",
                "concurrency": 190,
                "audit_batch_size": 1,
                "completion_batch_size": 1,
            },
        )

    def test_keyed_scheduler_keeps_zero_worker_key_disabled_when_scaled(self):
        config = GeneratorConfig(
            api_key="key-one",
            base_url="http://test",
            model="test",
            llm_workers=50,
            skip_ipf=True,
        )
        with patch.dict(
            "os.environ",
            {
                "MY_MODEL_API_KEYS": "key-two,key-three",
                "MY_MODEL_API_KEY_RPMS": "500,10,1000",
                "MY_MODEL_API_KEY_MODELS": "test,test,glm5-2",
                "MY_MODEL_API_KEY_CONCURRENCIES": "64,0,64",
                "MY_MODEL_API_KEY_AUDIT_BATCH_SIZES": "50,50,1",
                "MY_MODEL_API_KEY_COMPLETION_BATCH_SIZES": "25,25,1",
            },
            clear=False,
        ):
            generator = StubLLMGenerator(config, targets=[])
            generator.ensure_llm_config()

        self.assertEqual(generator._api_key_concurrencies, (25, 0, 25))

    def test_single_item_request_shaping_preserves_parent_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint_dir = Path(temporary) / "checkpoint"
            common = {
                "seed": 31,
                "api_key": "test",
                "base_url": "http://test",
                "model": "test",
                "audit_batch_size": 2,
                "llm_workers": 1,
                "llm_requests_per_minute": 0,
                "skip_ipf": True,
                "checkpoint_dir": checkpoint_dir,
            }
            first = StubLLMGenerator(GeneratorConfig(**common), targets=[])
            seeds = [make_seed("shaped_0"), make_seed("shaped_1")]
            first._llm_request_context.pinned_api_key_index = 0
            first._llm_request_context.request_batch_size = 1
            result = first._call_llm_batch_with_id_split(
                seeds,
                prompt_builder=build_seed_audit_prompt,
                temperature=0.0,
                stage="LLM seed audit",
                required_fields=("decision", "reason_code", "reason"),
                allowed_values={"decision": {"accept", "reject"}},
            )
            self.assertEqual(set(result), {"shaped_0", "shaped_1"})
            self.assertEqual(first.events, ["audit", "audit"])
            first.close_checkpoint()

            resumed = StubLLMGenerator(
                GeneratorConfig(**common, resume=True),
                targets=[],
            )
            accepted, _ = resumed.audit_candidate_seeds(
                [make_seed("shaped_0"), make_seed("shaped_1")]
            )
            self.assertEqual(len(accepted), 2)
            self.assertEqual(resumed.events, [])
            self.assertEqual(resumed.checkpoint_report()["cache_hits"], 1)
            resumed.close_checkpoint()

    def test_resume_reuses_cached_child_before_requesting_parent_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint_dir = Path(temporary) / "checkpoint"
            common = {
                "seed": 31,
                "api_key": "test",
                "base_url": "http://test",
                "model": "test",
                "audit_batch_size": 4,
                "llm_workers": 1,
                "llm_requests_per_minute": 0,
                "llm_retries": 0,
                "skip_ipf": True,
                "checkpoint_dir": checkpoint_dir,
            }
            seeds = [make_seed(f"resume_{index}") for index in range(4)]

            first = StubLLMGenerator(GeneratorConfig(**common), targets=[])
            first._call_llm_batch_with_id_split(
                seeds[:2],
                prompt_builder=build_seed_audit_prompt,
                temperature=0.0,
                stage="LLM seed audit",
                required_fields=("decision", "reason_code", "reason"),
                allowed_values={"decision": {"accept", "reject"}},
            )
            first.close_checkpoint()

            resumed = StubLLMGenerator(
                GeneratorConfig(**common, resume=True),
                targets=[],
            )
            requested_batch_sizes = []
            original_call = resumed.call_llm_json

            def record_batch_size(prompt, temperature):
                candidates = json.loads(prompt.split("Candidates:\n", 1)[1])
                requested_batch_sizes.append(len(candidates))
                return original_call(prompt, temperature)

            with patch.object(resumed, "call_llm_json", side_effect=record_batch_size):
                result = resumed._call_llm_batch_with_id_split(
                    seeds,
                    prompt_builder=build_seed_audit_prompt,
                    temperature=0.0,
                    stage="LLM seed audit",
                    required_fields=("decision", "reason_code", "reason"),
                    allowed_values={"decision": {"accept", "reject"}},
                )

            self.assertEqual(set(result), {f"resume_{index}" for index in range(4)})
            self.assertEqual(requested_batch_sizes, [2])
            resumed.close_checkpoint()

            fully_resumed = StubLLMGenerator(
                GeneratorConfig(**common, resume=True),
                targets=[],
            )
            accepted, _ = fully_resumed.audit_candidate_seeds(seeds)
            self.assertEqual(len(accepted), 4)
            self.assertEqual(fully_resumed.events, [])
            fully_resumed.close_checkpoint()

    def test_provider_health_exception_is_not_split_into_smaller_batches(self):
        generator = SyntheticProfileGenerator(
            GeneratorConfig(
                api_key="test",
                base_url="http://test",
                model="test",
                skip_ipf=True,
                llm_retries=0,
            ),
            targets=[],
        )
        seeds = [make_seed("health_0"), make_seed("health_1")]
        with patch.object(
            generator,
            "_call_llm_batch_results",
            side_effect=ProviderHealthExceeded("provider health exceeded"),
        ) as call:
            with self.assertRaises(ProviderHealthExceeded):
                generator._call_llm_batch_with_id_split(
                    seeds,
                    prompt_builder=build_seed_audit_prompt,
                    temperature=0.0,
                    stage="LLM seed audit",
                    required_fields=("decision", "reason_code", "reason"),
                    allowed_values={"decision": {"accept", "reject"}},
                )
        self.assertEqual(call.call_count, 1)

    def test_call_llm_recycles_provider_client_after_every_request(self):
        instances = []

        class FakeCompletions:
            def create(self, **_kwargs):
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content='{"ok": true}')
                        )
                    ],
                    usage=None,
                )

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())
                self.closed = False
                instances.append(self)

            def close(self):
                self.closed = True

        generator = SyntheticProfileGenerator(
            GeneratorConfig(
                api_key="test",
                base_url="http://test",
                model="test",
                skip_ipf=True,
            ),
            targets=[],
        )
        generator.ensure_llm_config()
        generator._llm_request_context.api_key_index = 0
        fake_openai = SimpleNamespace(OpenAI=FakeOpenAI)
        with patch.dict(sys.modules, {"openai": fake_openai}):
            self.assertEqual(generator.call_llm("one", temperature=0.0), '{"ok": true}')
            self.assertEqual(generator.call_llm("two", temperature=0.0), '{"ok": true}')

        self.assertEqual(len(instances), 2)
        self.assertTrue(all(instance.closed for instance in instances))

    def test_dedicated_generation_provider_does_not_replace_audit_provider(self):
        clients = []
        requests = []

        class FakeCompletions:
            def create(self, **kwargs):
                requests.append(kwargs)
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content='{"ok": true}')
                        )
                    ],
                    usage=None,
                )

        class FakeOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions())
                clients.append(self)

            def close(self):
                pass

        with patch.dict(
            "os.environ",
            {
                "PROFILE_GENERATION_API_KEY": "generation-key",
                "PROFILE_GENERATION_BASE_URL": "https://generation.test",
                "PROFILE_GENERATION_MODEL": "deepseek-v4-flash",
                "PROFILE_GENERATION_CONCURRENCY": "2",
                "PROFILE_GENERATION_BATCH_SIZE": "3",
                "PROFILE_GENERATION_RPM": "0",
                "MY_MODEL_API_KEYS": "",
                "MY_MODEL_API_KEY_RPMS": "",
                "MY_MODEL_API_KEY_MODELS": "",
                "MY_MODEL_API_KEY_CONCURRENCIES": "",
            },
            clear=False,
        ):
            generator = SyntheticProfileGenerator(
                GeneratorConfig(
                    api_key="audit-key",
                    base_url="https://audit.test",
                    model="glm-5.2",
                    llm_workers=2,
                    skip_ipf=True,
                ),
                targets=[],
            )
            generator.ensure_llm_config()
            fake_openai = SimpleNamespace(OpenAI=FakeOpenAI)
            with patch.dict(sys.modules, {"openai": fake_openai}):
                generator._llm_request_context.api_key_index = 0
                generator.call_llm("audit", temperature=0.0)
                generator._llm_request_context.provider_role = "generation"
                generator.call_llm("generation", temperature=0.0)

            contexts = []

            def worker(batch):
                contexts.append(
                    (
                        generator._llm_request_context.provider_role,
                        generator._llm_request_context.request_batch_size,
                    )
                )
                return batch

            generator._run_llm_batches(
                [[{"id": 1}], [{"id": 2}]],
                worker,
                "test generation",
                batch_family="completion",
            )

        self.assertEqual(clients[0].kwargs["base_url"], "https://audit.test")
        self.assertEqual(clients[0].kwargs["api_key"], "audit-key")
        self.assertEqual(requests[0]["model"], "glm-5.2")
        self.assertEqual(
            clients[1].kwargs["base_url"],
            "https://generation.test",
        )
        self.assertEqual(clients[1].kwargs["api_key"], "generation-key")
        self.assertEqual(requests[1]["model"], "deepseek-v4-flash")
        self.assertEqual(sorted(contexts), [("generation", 3), ("generation", 3)])
        self.assertEqual(
            generator._llm_usage["generation_scheduler"]["model_alias"],
            "deepseek-v4-flash",
        )

    def test_call_llm_watchdog_closes_a_stuck_provider_request(self):
        closed = threading.Event()

        class BlockingCompletions:
            def create(self, **_kwargs):
                if not closed.wait(1.0):
                    raise AssertionError("watchdog did not close the client")
                raise ValueError("transport closed by watchdog")

        class BlockingOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=BlockingCompletions())

            def close(self):
                closed.set()

        generator = SyntheticProfileGenerator(
            GeneratorConfig(
                api_key="test",
                base_url="http://test",
                model="test",
                llm_timeout=0.02,
                skip_ipf=True,
            ),
            targets=[],
        )
        generator.ensure_llm_config()
        generator._llm_request_context.api_key_index = 0
        fake_openai = SimpleNamespace(OpenAI=BlockingOpenAI)
        with patch.dict(sys.modules, {"openai": fake_openai}):
            with self.assertRaisesRegex(RuntimeError, "transport closed by watchdog"):
                generator.call_llm("stuck", temperature=0.0)

        self.assertTrue(closed.is_set())

    def test_retry_health_mode_keeps_transient_errors_in_request_retry_path(self):
        class FailingCompletions:
            def create(self, **_kwargs):
                raise TimeoutError("Request timed out.")

        class FailingOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FailingCompletions())

            def close(self):
                pass

        generator = SyntheticProfileGenerator(
            GeneratorConfig(
                api_key="test",
                base_url="http://test",
                model="test",
                skip_ipf=True,
            ),
            targets=[],
        )
        generator.ensure_llm_config()
        generator._llm_request_context.api_key_index = 0
        generator._provider_health = RollingFailureWindow(
            window_size=1,
            threshold=0.0,
            min_samples=1,
        )
        fake_openai = SimpleNamespace(OpenAI=FailingOpenAI)
        with (
            patch.dict(sys.modules, {"openai": fake_openai}),
            patch.dict(
                "os.environ",
                {"MY_MODEL_PROVIDER_HEALTH_MODE": "retry"},
                clear=False,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider request failed"):
                generator.call_llm("retry", temperature=0.0)

        self.assertFalse(generator._provider_halted.is_set())

    def test_pii_repair_propagates_provider_health_exception(self):
        generator = SyntheticProfileGenerator(
            GeneratorConfig(
                api_key="test",
                base_url="http://test",
                model="test",
                skip_ipf=True,
                llm_workers=1,
                llm_retries=0,
                pii_field_retries=1,
            ),
            targets=[],
        )
        profile = {
            **make_seed("health_repair"),
            "name": "Mina Chen",
            "email": "invalid-email",
            "phone_number": "+1 416 555 0138",
            "government_id": "482683399",
        }
        with patch.object(
            generator,
            "_call_llm_batch_with_id_split",
            side_effect=ProviderHealthExceeded("provider health exceeded"),
        ):
            with self.assertRaises(ProviderHealthExceeded):
                generator._validate_and_repair_pii([profile])

    def test_checkpoint_resume_reuses_completed_llm_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint_dir = Path(temporary) / "checkpoint"
            common = {
                "seed": 29,
                "api_key": "test",
                "base_url": "http://test",
                "model": "test",
                "skip_ipf": True,
                "checkpoint_dir": checkpoint_dir,
            }
            first = StubLLMGenerator(GeneratorConfig(**common), targets=[])
            first_seed = make_seed("checkpoint_1")
            first_audited, _ = first.audit_candidate_seeds([first_seed])
            first.complete_profiles_with_llm(first_audited)

            self.assertEqual(first.events, ["audit", "completion"])
            self.assertTrue((checkpoint_dir / "manifest.json").is_file())
            self.assertTrue((checkpoint_dir / "llm_batches.sqlite3").is_file())
            first.close_checkpoint()

            resumed = StubLLMGenerator(
                GeneratorConfig(**common, resume=True),
                targets=[],
            )
            resumed_seed = make_seed("checkpoint_1")
            resumed_audited, _ = resumed.audit_candidate_seeds([resumed_seed])
            resumed.complete_profiles_with_llm(resumed_audited)

            self.assertEqual(resumed.events, [])
            self.assertEqual(resumed.checkpoint_report()["cache_hits"], 2)
            self.assertEqual(resumed._llm_usage["requests"], 2)
            resumed.close_checkpoint()

            with self.assertRaisesRegex(RuntimeError, "do not match"):
                StubLLMGenerator(
                    GeneratorConfig(**{**common, "seed": 30}, resume=True),
                    targets=[],
                )

    def test_resume_quarantines_semantically_invalid_government_id_audit_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint_dir = Path(temporary) / "checkpoint"
            common = {
                "seed": 37,
                "api_key": "test",
                "base_url": "http://test",
                "model": "test",
                "audit_batch_size": 1,
                "llm_workers": 1,
                "llm_retries": 0,
                "skip_ipf": True,
                "checkpoint_dir": checkpoint_dir,
            }
            profile = {
                **make_seed("invalid_cached_audit"),
                "name": "Mina Chen",
                "email": "mina@example.net",
                "phone_number": "+1 416 555 0138",
                "government_id": "482683399",
            }
            first = StubLLMGenerator(GeneratorConfig(**common), targets=[])
            first.audit_government_id_consistency_with_llm(
                [profile],
                phase="initial",
            )
            first.close_checkpoint()

            database_path = checkpoint_dir / "llm_batches.sqlite3"
            invalid_result = json.dumps(
                {
                    "invalid_cached_audit": {
                        "candidate_id": "invalid_cached_audit",
                        "decision": "reject",
                        "reason_code": "none",
                        "reason": "",
                    }
                }
            )
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    UPDATE batch_cache
                    SET result_json = ?
                    WHERE stage = 'LLM government ID consistency audit'
                    """,
                    (invalid_result,),
                )
                connection.commit()

            resumed = StubLLMGenerator(
                GeneratorConfig(**common, resume=True),
                targets=[],
            )
            audits = resumed.audit_government_id_consistency_with_llm(
                [profile],
                phase="initial",
            )
            self.assertEqual(audits["invalid_cached_audit"]["decision"], "accept")
            self.assertEqual(resumed.events, ["government_id_audit"])
            self.assertEqual(
                resumed.checkpoint_report()["quarantined_batches"],
                1,
            )
            resumed.close_checkpoint()

            with sqlite3.connect(database_path) as connection:
                quarantined = connection.execute(
                    "SELECT COUNT(*) FROM quarantined_batch_cache"
                ).fetchone()[0]
            self.assertEqual(quarantined, 1)

    def test_resume_allows_only_increasing_backfill_ceiling(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint_dir = Path(temporary) / "checkpoint"
            common = {
                "seed": 41,
                "api_key": "test",
                "base_url": "http://test",
                "model": "test",
                "skip_ipf": True,
                "checkpoint_dir": checkpoint_dir,
            }
            original = StubLLMGenerator(
                GeneratorConfig(**common, max_backfill_rounds=3),
                targets=[],
            )
            original.close_checkpoint()

            extended = StubLLMGenerator(
                GeneratorConfig(
                    **common,
                    max_backfill_rounds=6,
                    resume=True,
                ),
                targets=[],
            )
            self.assertTrue(
                extended.checkpoint_report()["extended_backfill_ceiling"]
            )
            extended.close_checkpoint()

            with self.assertRaisesRegex(RuntimeError, "do not match"):
                StubLLMGenerator(
                    GeneratorConfig(
                        **common,
                        max_backfill_rounds=2,
                        resume=True,
                    ),
                    targets=[],
                )

    def test_positive_target_cells_are_detected_and_backfilled(self):
        targets = [
            IPFConstraint(
                "country_education",
                ("current_country", "education_level"),
                {
                    make_key(["Japan", "doctorate"]): 0.5,
                    make_key(["Canada", "bachelor"]): 0.5,
                    make_key(["Japan", "none"]): 0.0,
                },
            ),
            IPFConstraint(
                "older_women",
                ("age_group", "sex"),
                {make_key(["65-80", "female"]): 1.0},
            ),
        ]
        generator = StubLLMGenerator(
            GeneratorConfig(
                seed=9,
                api_key="test",
                base_url="http://test",
                model="test",
            ),
            targets=targets,
        )
        base, _ = generator.process_candidate_batch([make_seed("base")])
        missing = generator.find_missing_target_cells(base)
        missing_keys = {item["key"] for item in missing}
        self.assertIn("Japan | doctorate", missing_keys)
        self.assertIn("65-80 | female", missing_keys)
        self.assertNotIn("Japan | none", missing_keys)

        extra_seeds = generator.generate_targeted_candidates(missing)
        extra, _ = generator.process_candidate_batch(extra_seeds)
        combined = base + extra
        self.assertEqual(generator.find_missing_target_cells(combined), [])
        self.assertTrue(any(item["current_location"].endswith(", Japan") and item["education_level"] == "doctorate" for item in combined))
        self.assertTrue(any(65 <= item["age"] <= 80 and item["sex"] == "female" for item in combined))

    def test_targeted_raw_location_fields_keep_country_aliases_in_sync(self):
        generator = SyntheticProfileGenerator(GeneratorConfig(seed=11), targets=[])
        candidate = make_seed("sync")

        generator._force_bucket_value(candidate, "citizenship", "Japan")
        generator._force_bucket_value(candidate, "current_location", "Berlin, Germany")
        generator._force_bucket_value(candidate, "birth_location", "Lagos, Nigeria")

        self.assertEqual(candidate["_citizenship_country"], "Japan")
        self.assertEqual(candidate["_current_country"], "Germany")
        self.assertEqual(candidate["_birth_country"], "Nigeria")

    def test_derived_joint_target_buckets_follow_seed_fields(self):
        candidate = make_seed("derived")

        self.assertEqual(bucket_value(candidate, "employment_status"), "employed")
        self.assertEqual(bucket_value(candidate, "ilo_education_group"), "advanced")
        self.assertEqual(
            bucket_value(candidate, "welfare_education_group"), "tertiary"
        )
        self.assertEqual(bucket_value(candidate, "birth_migration_status"), "foreign")
        self.assertEqual(
            bucket_value(candidate, "citizenship_migration_status"), "local"
        )

        candidate["occupation"] = "None"
        candidate["education_level"] = "primary"
        self.assertEqual(
            bucket_value(candidate, "employment_status"), "not employed"
        )
        self.assertEqual(bucket_value(candidate, "ilo_education_group"), "basic")
        self.assertEqual(
            bucket_value(candidate, "welfare_education_group"), "primary"
        )

    def test_default_target_proposal_preserves_random_exploration(self):
        config = GeneratorConfig(seed=23, target_proposal_ratio=0.9)
        generator = SyntheticProfileGenerator(config)
        seeds = generator.generate_candidates(1000)

        self.assertGreater(generator._target_proposal_seed_count, 800)
        self.assertGreater(generator._uniform_seed_count, 50)
        same_citizenship = sum(
            item["_current_country"] == item["_citizenship_country"] for item in seeds
        )
        same_birth = sum(item["_current_country"] == item["_birth_country"] for item in seeds)
        self.assertGreater(same_citizenship / len(seeds), 0.75)
        self.assertGreater(same_birth / len(seeds), 0.65)

    def test_weighted_sampling_is_without_replacement(self):
        generator = SyntheticProfileGenerator(
            GeneratorConfig(seed=3, skip_ipf=True, sample_trials=1), targets=[]
        )
        candidates = []
        for index in range(5):
            candidate = make_seed(f"cand_{index}", age=30 + index)
            candidate["_weight"] = 1e12 if index == 0 else 1.0
            candidates.append(candidate)
        sample = generator.sample_profiles(candidates, 4)
        ids = [item["_candidate_id"] for item in sample]
        self.assertEqual(len(ids), 4)
        self.assertEqual(len(set(ids)), 4)
        with self.assertRaises(ValueError):
            generator.sample_profiles(candidates, 6)

    def test_target_capacity_check_catches_unique_sampling_shortage(self):
        target = IPFConstraint(
            "sex_target",
            ("sex",),
            {make_key(["female"]): 0.75, make_key(["male"]): 0.25},
        )
        generator = SyntheticProfileGenerator(GeneratorConfig(skip_ipf=False), targets=[target])
        candidates = [
            make_seed("f", sex="female"),
            make_seed("m1", sex="male", age=35),
            make_seed("m2", sex="male", age=36),
            make_seed("m3", sex="male", age=37),
        ]

        deficits = generator.find_target_capacity_deficits(candidates, output_count=4)

        self.assertEqual(
            deficits,
            [
                {
                    "constraint": "sex_target",
                    "fields": ("sex",),
                    "key": "female",
                    "available": 1,
                    "required": 3,
                }
            ],
        )

    def test_capacity_backfill_generates_each_missing_copy(self):
        target = IPFConstraint(
            "sex_target",
            ("sex",),
            {make_key(["female"]): 0.75, make_key(["male"]): 0.25},
        )
        generator = SyntheticProfileGenerator(
            GeneratorConfig(seed=31, skip_ipf=False), targets=[target]
        )
        candidates = [
            make_seed("f", sex="female"),
            make_seed("m1", sex="male", age=35),
            make_seed("m2", sex="male", age=36),
            make_seed("m3", sex="male", age=37),
        ]

        deficits = generator.find_target_capacity_deficits(candidates, output_count=4)
        backfill = generator.generate_capacity_backfill_candidates(deficits)

        self.assertEqual(len(backfill), 2)
        self.assertTrue(all(item["sex"] == "female" for item in backfill))
        self.assertEqual(
            generator.find_target_capacity_deficits(
                candidates + backfill, output_count=4
            ),
            [],
        )

    def test_final_audit_uses_realized_sample(self):
        target = IPFConstraint(
            "sex_balance",
            ("sex",),
            {make_key(["female"]): 0.5, make_key(["male"]): 0.5},
        )
        generator = SyntheticProfileGenerator(GeneratorConfig(skip_ipf=True), targets=[target])
        all_female = [make_seed("f1", age=30), make_seed("f2", age=31)]
        balanced = [make_seed("f", sex="female"), make_seed("m", sex="male", age=35)]
        self.assertEqual(generator.audit_final_sample(all_female)["final_l1"]["sex_balance"], 1.0)
        self.assertEqual(generator.audit_final_sample(balanced)["final_l1"]["sex_balance"], 0.0)

    def test_generate_runs_new_pipeline_end_to_end_with_stub_llm(self):
        target = IPFConstraint(
            "sex_balance",
            ("sex",),
            {make_key(["female"]): 0.5, make_key(["male"]): 0.5},
        )
        config = GeneratorConfig(
            count=6,
            candidate_count=8,
            seed=5,
            api_key="test",
            base_url="http://test",
            model="test",
            audit_batch_size=2,
            completion_batch_size=2,
            llm_workers=3,
            sample_trials=3,
            require_full_skeleton_ipf=False,
        )
        generator = StubLLMGenerator(config, targets=[target])
        profiles, report = generator.generate()

        self.assertEqual(len(profiles), 6)
        self.assertTrue(all(set(profile) == set(FULL_SCHEMA_FIELDS) for profile in profiles))
        self.assertTrue(all(check_profile(profile) == [] for profile in profiles))
        self.assertEqual(len({profile["government_id"] for profile in profiles}), 6)
        self.assertIn("ipf", generator.events)
        ipf_index = generator.events.index("ipf")
        audit_positions = [i for i, event in enumerate(generator.events) if event == "audit"]
        completion_positions = [
            i for i, event in enumerate(generator.events) if event == "completion"
        ]
        government_id_audit_positions = [
            i
            for i, event in enumerate(generator.events)
            if event == "government_id_audit"
        ]
        self.assertLess(min(audit_positions), min(completion_positions))
        self.assertLess(max(audit_positions), ipf_index)
        self.assertLess(max(completion_positions), ipf_index)
        self.assertLess(max(government_id_audit_positions), ipf_index)
        self.assertTrue(all(event == "ipf" for event in generator.events[ipf_index:]))
        self.assertEqual(report["llm_seed_rejected"], 0)
        self.assertEqual(report["llm_workers"], 3)
        self.assertEqual(report["candidate_count"], 8)
        self.assertEqual(report["base_seed_count"], 8)
        self.assertEqual(report["total_audited_seed_count"], 8)
        self.assertEqual(report["logical_screen_survival_rate"], 1.0)
        self.assertEqual(report["completion_survival_rate"], 1.0)
        self.assertEqual(report["replenish_seed_count"], 0)
        self.assertIn("backfill_seed_count", report)
        self.assertNotIn("hard_kept", report)
        self.assertNotIn("hard_dropped", report)
        self.assertNotIn("seed_program_accepted", report)
        self.assertNotIn("seed_program_rejected", report)
        self.assertIn("final_audit", report)


if __name__ == "__main__":
    unittest.main()
