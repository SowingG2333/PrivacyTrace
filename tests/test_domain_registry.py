import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent_env.domain_registry import (  # noqa: E402
    load_available_server_names,
    load_domain_registry,
    select_servers,
    validate_domain_servers,
)


EXPECTED_DOMAINS = {"travel", "health", "shopping", "career_learning"}
EXPECTED_DOMAIN_SERVER_COUNTS = {
    "travel": 10,
    "health": 8,
    "shopping": 6,
    "career_learning": 6,
}


class DomainRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_domain_registry(PROJECT_ROOT / "config/domains.json")
        self.available = load_available_server_names(
            PROJECT_ROOT / "mcp_servers/commands.json"
        )

    def test_registry_covers_expected_domains(self):
        self.assertEqual(set(self.registry), EXPECTED_DOMAINS)

    def test_all_configured_servers_exist_in_catalog(self):
        validate_domain_servers(self.registry, self.available)

    def test_domain_server_counts_include_extensions(self):
        self.assertEqual(
            {
                domain: len(spec.core_servers)
                for domain, spec in self.registry.items()
            },
            EXPECTED_DOMAIN_SERVER_COUNTS,
        )

        self.assertIn(
            "Duffel Flight Search",
            self.registry["travel"].core_servers,
        )
        self.assertNotIn(
            "OctoTrip Flights",
            self.registry["travel"].core_servers,
        )
        self.assertIn("OneBusAway", self.registry["travel"].core_servers)
        self.assertIn("tickadoo", self.registry["travel"].core_servers)
        self.assertIn("DailyMed MCP", self.registry["health"].core_servers)
        self.assertIn("Open Food Facts", self.registry["health"].core_servers)
        self.assertIn("OpenFDA", self.registry["health"].core_servers)
        self.assertIn("Keenable Web Search", self.registry["shopping"].core_servers)
        self.assertIn(
            "Remoote Jobs", self.registry["career_learning"].core_servers
        )
        self.assertNotIn(
            "NixOS", self.registry["career_learning"].core_servers
        )
        self.assertNotIn(
            "Reddit", self.registry["career_learning"].core_servers
        )
        self.assertEqual(
            self.registry["career_learning"].optional_servers,
            (),
        )

    def test_selection_is_ordered_and_contains_every_configured_server(self):
        selected = select_servers("travel", self.registry)

        self.assertEqual(selected[:2], ["Geoapify", "Weather Data"])
        self.assertIn("National Parks", selected)

    def test_default_policy_can_select_every_domain_server(self):
        for domain, spec in self.registry.items():
            self.assertEqual(
                select_servers(domain, self.registry),
                list(
                    dict.fromkeys(
                        (*spec.core_servers, *spec.optional_servers)
                    )
                ),
            )

    def test_travel_tool_allowlists_reduce_the_default_catalog_to_forty_three(self):
        travel = self.registry["travel"]
        allowlists = travel.select_tool_allowlists()

        self.assertEqual(set(allowlists), {"OneBusAway", "tickadoo"})
        self.assertEqual(len(allowlists["OneBusAway"]), 5)
        self.assertEqual(len(allowlists["tickadoo"]), 4)
        self.assertNotIn("onebusaway_get_vehicles", allowlists["OneBusAway"])
        self.assertNotIn("report_quality_signal", allowlists["tickadoo"])

        unfiltered_travel_tools = {
            "Geoapify": 7,
            "Weather Data": 4,
            "Time MCP": 2,
            "Wikipedia": 9,
            "National Parks": 6,
            "Metropolitan Museum": 3,
            "Movie Recommender": 1,
            "Duffel Flight Search": 2,
        }
        self.assertEqual(
            sum(unfiltered_travel_tools.values())
            + len(allowlists["OneBusAway"])
            + len(allowlists["tickadoo"]),
            43,
        )

    def test_health_tool_allowlists_reduce_the_default_catalog_to_forty_one(self):
        health = self.registry["health"]
        allowlists = health.select_tool_allowlists()

        self.assertEqual(
            set(allowlists),
            {
                "BioMCP",
                "Medical Calculator",
                "Open Food Facts",
                "OpenFDA",
            },
        )
        self.assertEqual(len(allowlists["BioMCP"]), 5)
        self.assertEqual(len(allowlists["Medical Calculator"]), 7)
        self.assertEqual(len(allowlists["Open Food Facts"]), 3)
        self.assertEqual(len(allowlists["OpenFDA"]), 3)
        self.assertNotIn("trial_searcher", allowlists["BioMCP"])
        self.assertNotIn("gene_getter", allowlists["BioMCP"])
        self.assertNotIn("variant_searcher", allowlists["BioMCP"])
        self.assertNotIn("nci_biomarker_searcher", allowlists["BioMCP"])
        self.assertNotIn("openfda_label_searcher", allowlists["BioMCP"])
        self.assertNotIn(
            "openfda_search_adverse_events",
            allowlists["OpenFDA"],
        )

        unfiltered_health_tools = {
            "Geoapify": 7,
            "Wikipedia": 9,
            "FruityVice": 1,
            "DailyMed MCP": 6,
        }
        self.assertEqual(
            sum(unfiltered_health_tools.values())
            + len(allowlists["BioMCP"])
            + len(allowlists["Medical Calculator"])
            + len(allowlists["Open Food Facts"])
            + len(allowlists["OpenFDA"]),
            41,
        )

    def test_tool_allowlist_rejects_an_unselected_server(self):
        config = {
            "health": {
                "description": "test",
                "core_servers": ["BioMCP"],
                "tool_allowlists": {"Wikipedia": ["get_summary"]},
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "domains.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unselected servers"):
                load_domain_registry(path)

    def test_unknown_configured_server_is_rejected(self):
        modified = dict(self.registry)
        modified["health"] = replace(
            modified["health"],
            core_servers=(*modified["health"].core_servers, "Not A Real Server"),
        )
        with self.assertRaises(ValueError):
            validate_domain_servers(modified, self.available)


if __name__ == "__main__":
    unittest.main()
