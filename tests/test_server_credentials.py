import json
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ServerCredentialTests(unittest.TestCase):
    def test_catalog_declares_weather_and_tmdb_credentials(self):
        commands = json.loads(
            (PROJECT_ROOT / "mcp_servers/commands.json").read_text(encoding="utf-8")
        )
        self.assertIn("WEATHER_API_KEY", commands["Weather Data"]["env"])
        self.assertIn("TMDB_API_KEY", commands["Movie Recommender"]["env"])
        self.assertEqual(
            commands["Duffel Flight Search"]["env"],
            ["DUFFEL_ACCESS_TOKEN"],
        )

    def test_runtime_servers_do_not_hardcode_api_keys(self):
        paths = [
            PROJECT_ROOT / "mcp_servers/weather_mcp/server.py",
            PROJECT_ROOT
            / "mcp_servers/movie-recommender-mcp/movie-reccomender-mcp/movie_recommender.py",
            PROJECT_ROOT / "mcp_servers/duffel-flight-mcp/server.py",
        ]
        hardcoded = re.compile(
            r"API_KEY\s*=\s*['\"][A-Za-z0-9_./+=-]{16,}['\"]"
        )
        for path in paths:
            if not path.is_file():
                continue
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(hardcoded.search(source), path)
            self.assertIn("os.getenv", source)


if __name__ == "__main__":
    unittest.main()
