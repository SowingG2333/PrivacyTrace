# PrivacyTrace

Anonymous code and data release for peer review at ICLR 2027.

PrivacyTrace measures what a tool-using language-model agent can reveal about a
synthetic user from MCP tool-call traces. The released pipeline covers four
stages: calibrated profile synthesis, profile-conditioned task construction,
agent trajectory collection, and semantic privacy-leakage evaluation.

The study contains 1,000 synthetic profiles with 17 attributes, 4,000 tasks in
four domains, 4,000 trajectories, and 67,950 recorded tool events across 25 MCP
servers. No human-subject data were intentionally collected. Tool responses can
nevertheless contain public third-party content; read [DATA_CARD.md](DATA_CARD.md)
before downloading the full release.

## Repository contents

```text
agent_env/        Agent runtime, MCP lifecycle, and trajectory schema
privacy_trace/    Profile, scenario, attack, and evaluation logic
llm/              OpenAI-compatible provider adapter
config/           Frozen domain, policy, and tool-schema configuration
data/             Public statistical inputs and calibrated targets
mcp_servers/      Licensed snapshots, project adapters, and source manifest
scripts/          Reproduction, download, packaging, and validation commands
tests/            Unit and regression tests
examples/         Small offline examples, one from each task domain
results/paper/    Frozen aggregate tables and publication figures
release/          Data manifest and SHA-256 checksums
release_assets/   Checksum-verified experiment archives stored with Git LFS
```

The manuscript source is deliberately not distributed in this anonymous
repository. Full experiment records are stored as Git LFS objects so the
anonymous review proxy can serve them without exposing the source repository.

## Installation

The reference environment is Python 3.10 on Linux. A clean virtual environment
is recommended:

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-lock.txt
```

The unlocked [requirements.txt](requirements.txt) documents supported ranges;
`requirements-lock.txt` is the review snapshot used by CI and validation.

MCP servers are optional for offline analysis. Inspect their provenance first:

```bash
python mcp_servers/install.py --check
```

Licensed source snapshots are included. Seven upstream projects without an
explicit license are not redistributed and are not fetched by default. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Five-minute offline check

The offline check requires no model key and makes no network requests:

```bash
python scripts/run_release_smoke.py
python scripts/validate_release.py --repo .
python -m unittest discover -s tests -p 'test_*.py'
```

It validates the example schemas, frozen aggregate manifest, MCP catalog, and
release metadata. Expected output ends with `offline release smoke test: PASS`.

## Download the full experiment data

After downloading or cloning the anonymous repository, extract and verify the
bundled Git LFS assets with:

```bash
python scripts/download_release_data.py --output artifacts/release --verify
```

The downloader verifies every SHA-256 digest before extraction. The three
assets contain profiles/scenarios, trajectories, and evaluation records. If a
plain Git clone contains only LFS pointer files, run `git lfs pull` first; the
downloadable archive served by the anonymous review proxy contains resolved
file contents.

## Reproduce paper tables and figures

With the full data installed:

```bash
python scripts/build_paper_results.py \
  --input-root artifacts/release \
  --output-root reproduced_results
```

Compare the generated CSV/JSON files with `results/paper/data/`. The script
does not call an LLM and reproduces the reported aggregate values from frozen
records. Generating profiles, trajectories, or new attacks does call external
models and services and may incur substantial cost.

The end-to-end stages remain independently reviewable:

```bash
python -m privacy_trace.profile_generator --count 1000
python -m privacy_trace.scenario_generator --n 1000
python scripts/run_agent_scenarios.py --domains travel health shopping career_learning
python scripts/run_privacy_attacks.py --attack-mode two_stage
```

Exact command options and schemas are documented in `docs/` and
`scripts/README.md`. Credentials belong only in local `.env` and `.env.mcp`
files, both of which are ignored.

## Runtime and reproducibility limitations

- Full trajectory generation requires multiple model endpoints and access to
  external MCP services. Runtime is measured in days rather than minutes.
- External APIs and remotely hosted MCP servers can change or become
  unavailable. Frozen tool schemas and completed records are therefore part of
  the release.
- Historical provider builds were not available for every model alias. Known
  versions and execution windows are recorded in the data manifest.
- Seven upstream MCP projects have no explicit redistribution license. Their
  sources are omitted; fetching them requires an explicit acknowledgement.
- Generated names, phone numbers, and government identifiers are synthetic but
  realistic-looking. They must not be treated as real identities.

## Review-only terms

The project-owned code and data are supplied solely for anonymous peer review.
No reuse license is granted during review; see
[REVIEW_LICENSE.md](REVIEW_LICENSE.md). Third-party components retain their own
licenses and attribution.
