#!/usr/bin/env python3
"""Build the paper-facing experiment manifest, tables, and figures.

The frozen experiment summaries predate a post-hoc Unicode-name audit.  This
script treats ``pii_unicode_posthoc_audit.json`` and the final experiment report
as the paper source of truth.  It validates their totals against the frozen
summaries, applies the same narrow correction to server-level rows, and writes
one versioned set of paper artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from privacy_trace.hybrid_judge import semantic_correct


REPO_ROOT = Path(__file__).resolve().parents[1]


def configure_paths(input_root: Path, output_root: Path) -> None:
    """Configure frozen-record inputs and generated-result outputs."""
    global ROOT, PAPER_DATA, FIGURES
    global MAIN_ANALYSIS, POSTHOC_AUDIT, CHANNEL_ANALYSIS, ATTACK_RECORDS
    global MODEL_GENERALIZATION, GENERALIZATION_RUNS
    global PROFILE_REPORT, SCENARIO_AUDIT, TRAJECTORY_AUDIT, FINAL_REPORT

    ROOT = input_root.resolve()
    PAPER_DATA = output_root.resolve() / "data"
    FIGURES = output_root.resolve() / "figures"
    experiments = ROOT / "artifacts/privacy_experiments"
    main_evaluation = experiments / "deepseek_v4_flash_0731/evaluation"
    MAIN_ANALYSIS = main_evaluation / "analysis/tool_call_privacy_analysis.json"
    POSTHOC_AUDIT = main_evaluation / "analysis/pii_unicode_posthoc_audit.json"
    CHANNEL_ANALYSIS = main_evaluation / "channel_ablation/analysis/channel_semantic_asr.json"
    ATTACK_RECORDS = main_evaluation / "attack_records.jsonl"
    MODEL_GENERALIZATION = experiments / "model_generalization_boyue_glm52/comparison.json"
    GENERALIZATION_RUNS = {
        "GPT-5.5": (
            experiments / "gpt_5_5/evaluation/attack_records.jsonl",
            experiments / "gpt_5_5/evaluation/judge_first",
        ),
        "DeepSeek-V4-Flash-0731": (
            main_evaluation / "model_generalization_boyue/attack_records.jsonl",
            main_evaluation / "model_generalization_boyue/judge_first",
        ),
        "MiniMax-M3": (
            experiments / "minimax_m3/evaluation/attack_records.jsonl",
            experiments / "minimax_m3/evaluation/judge_first",
        ),
        "Gemini-3-Flash-Preview": (
            experiments / "gemini_3_flash_preview/evaluation/attack_records.jsonl",
            experiments / "gemini_3_flash_preview/evaluation/judge_first",
        ),
    }
    PROFILE_REPORT = ROOT / "artifacts/profile_pool/generation_report.json"
    SCENARIO_AUDIT = ROOT / "artifacts/scenario/combined_audit.json"
    TRAJECTORY_AUDIT = ROOT / "artifacts/trajectories/combined_audit.json"
    FINAL_REPORT = main_evaluation / "analysis/experiment_report.md"


configure_paths(REPO_ROOT, REPO_ROOT / "results/paper")


COLORS = {
    "navy": "#315A7D",
    "blue": "#5E8FB2",
    "teal": "#3A8D86",
    "amber": "#D39A3A",
    "coral": "#C66A61",
    "slate": "#7B8794",
    "gray": "#A8AFB7",
    "light_gray": "#E7EAED",
    "grid": "#E9ECEF",
    "ink": "#263238",
}


ATTRIBUTE_RESULTS = [
    ("citizenship", 7.050, 57.725, 50.675, 2309),
    ("ethnicity", 9.800, 49.850, 40.050, 1994),
    ("current location", 0.250, 38.400, 38.150, 1536),
    ("religious belief", 20.400, 50.025, 29.625, 2001),
    ("age", 2.825, 15.500, 12.675, 620),
    ("physical condition", 24.800, 30.325, 5.525, 1213),
    ("occupation", 0.650, 5.650, 5.000, 226),
    ("birth location", 0.300, 1.425, 1.125, 57),
    ("name", 0.000, 0.850, 0.850, 34),
    ("phone number", 0.000, 0.025, 0.025, 1),
    ("email", 0.000, 0.000, 0.000, 0),
    ("government ID", 0.000, 0.000, 0.000, 0),
    ("education level", 12.600, 10.250, -2.350, 410),
    ("sex", 50.100, 46.225, -3.875, 1849),
    ("income level", 29.100, 25.025, -4.075, 1001),
    ("mental condition", 81.400, 76.625, -4.775, 3065),
    ("relationship status", 66.225, 38.400, -27.825, 1536),
]

# These are the final report values after the Unicode-name correction.  The
# frozen JSON has the corresponding pre-correction values, which are asserted
# below so that a changed input cannot silently enter the paper.
DOMAIN_RESULTS = [
    ("Career learning", 3.600, 2.894, 4.300, 21.571),
    ("Health", 5.400, 4.594, 6.218, 23.247),
    ("Shopping", 13.718, 12.941, 14.494, 31.865),
    ("Travel", 10.412, 9.665, 11.147, 28.329),
]

CALL_COUNT_RESULTS = [
    ("0", -4.385, 55),
    ("1--5", 2.875, 540),
    ("6--10", 6.018, 866),
    ("11--20", 9.995, 1479),
    ("21--40", 11.647, 948),
    ("41+", 9.552, 101),
]

REPRESENTATION_RESULTS = [
    {
        "representation": "Lossless catalog",
        "correct": 17852,
        "slots": 68000,
        "asr_pct": 26.253,
        "prompt_tokens_p50": 30393,
        "prompt_tokens_p90": 92271,
        "prompt_chars_p50": 117011,
        "prompt_chars_p90": 340217,
        "generation_failed_views": 11,
    },
    {
        "representation": "Raw repeated trace",
        "correct": 17954,
        "slots": 68000,
        "asr_pct": 26.403,
        "prompt_tokens_p50": 37520,
        "prompt_tokens_p90": 107413,
        "prompt_chars_p50": 137306,
        "prompt_chars_p90": 384869,
        "generation_failed_views": 13,
    },
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        raise ValueError(f"No rows for {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def old_ascii_normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def unicode_false_positives_by_server() -> Counter[str]:
    """Reproduce the documented narrow correction for single-server rows."""
    counts: Counter[str] = Counter()
    with ATTACK_RECORDS.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            is_false_positive = (
                row.get("arm") == "single_server"
                and row.get("attribute") == "name"
                and row.get("strict") is True
                and str(row.get("truth")) != str(row.get("prediction"))
                and not old_ascii_normalize(row.get("truth"))
                and not old_ascii_normalize(row.get("prediction"))
            )
            if is_false_positive:
                counts[str(row["server_name"])] += 1
    return counts


def load_judgments(root: Path) -> dict[str, dict[str, Any]]:
    judgments: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "batches").rglob("*.json")):
        if path.name.startswith("._"):
            continue
        batch = load_json(path)
        for item in batch.get("judgments", []):
            judgments[str(item["case_id"])] = item
    return judgments


def profile_bootstrap_interval(values: np.ndarray, *, samples: int = 10_000, seed: int = 42) -> list[float]:
    """Bootstrap profile-level rates in bounded chunks."""
    rng = np.random.default_rng(seed)
    draws: list[np.ndarray] = []
    remaining = samples
    while remaining:
        size = min(1_000, remaining)
        indices = rng.integers(0, len(values), size=(size, len(values)))
        draws.append(values[indices].mean(axis=1) * 100.0)
        remaining -= size
    estimates = np.concatenate(draws)
    return [round(float(np.quantile(estimates, 0.025)), 3), round(float(np.quantile(estimates, 0.975)), 3)]


def corrected_generalization_rows(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-score the fixed-judge replications with the Unicode audit rule."""
    frozen = {row["label"]: row for row in comparison["models"]}
    output: list[dict[str, Any]] = []
    for label, (records_path, judge_root) in GENERALIZATION_RUNS.items():
        judgments = load_judgments(judge_root)
        by_profile_correct: Counter[str] = Counter()
        by_profile_total: Counter[str] = Counter()
        frozen_correct = 0
        removed = 0
        with records_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                correct, _, _ = semantic_correct(row, judgments)
                false_positive = (
                    bool(correct)
                    and row.get("attribute") == "name"
                    and row.get("strict") is True
                    and str(row.get("truth")) != str(row.get("prediction"))
                    and not old_ascii_normalize(row.get("truth"))
                    and not old_ascii_normalize(row.get("prediction"))
                )
                profile = str(row["profile_id"])
                frozen_correct += int(correct)
                removed += int(false_positive)
                by_profile_correct[profile] += int(bool(correct) and not false_positive)
                by_profile_total[profile] += 1

        source = frozen[label]
        assert frozen_correct == source["correct"]
        profiles = sorted(by_profile_total)
        rates = np.array([by_profile_correct[p] / by_profile_total[p] for p in profiles])
        correct = sum(by_profile_correct.values())
        slots = sum(by_profile_total.values())
        interval = profile_bootstrap_interval(rates)
        output.append(
            {
                "model": label,
                "correct": correct,
                "slots": slots,
                "asr_pct": round(correct / slots * 100.0, 3),
                "ci95_low": interval[0],
                "ci95_high": interval[1],
                "generation_failed_views": source["generation_failed_views"],
                "unicode_false_positives_removed": removed,
            }
        )
    return output


def configure_plotting() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Nimbus Sans", "Helvetica", "Arial", "Liberation Sans"],
            "font.size": 7.0,
            "font.weight": "normal",
            "axes.labelsize": 7.0,
            "axes.titlesize": 7.2,
            "axes.titleweight": "normal",
            "xtick.labelsize": 6.5,
            "ytick.labelsize": 6.5,
            "legend.fontsize": 6.5,
            "text.color": COLORS["ink"],
            "axes.labelcolor": COLORS["ink"],
            "axes.titlecolor": COLORS["ink"],
            "xtick.color": COLORS["ink"],
            "ytick.color": COLORS["ink"],
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": COLORS["slate"],
            "axes.linewidth": 0.55,
            "grid.color": COLORS["grid"],
            "grid.linewidth": 0.5,
            "grid.alpha": 1.0,
            "lines.linewidth": 1.0,
            "lines.markersize": 3.8,
            "axes.unicode_minus": True,
            "text.antialiased": True,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
        }
    )


def save_figure(fig: plt.Figure, stem: str) -> None:
    fig.savefig(FIGURES / f"{stem}.pdf")
    fig.savefig(FIGURES / f"{stem}.png", dpi=220)
    plt.close(fig)


def panel_title(ax: plt.Axes, letter: str, title: str) -> None:
    ax.text(
        0.0,
        1.035,
        f"({letter})",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=7.3,
        fontweight="bold",
    )
    ax.text(
        0.085,
        1.035,
        title,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=7.2,
        fontweight="normal",
    )


def attribute_panel(ax: plt.Axes, rows: list[tuple[str, float, float, float, int]]) -> None:
    labels = [row[0] for row in rows]
    schema = np.array([row[1] for row in rows])
    catalog = np.array([row[2] for row in rows])
    lifts = np.array([row[3] for row in rows])
    y = np.arange(len(labels))

    bar_height = 0.32
    ax.barh(
        y - bar_height / 2,
        schema,
        height=bar_height,
        color=COLORS["gray"],
        edgecolor="white",
        linewidth=0.25,
        label="Schema-only",
        zorder=2,
    )
    ax.barh(
        y + bar_height / 2,
        catalog,
        height=bar_height,
        color=COLORS["navy"],
        edgecolor="white",
        linewidth=0.25,
        label="Full catalog",
        zorder=3,
    )
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlim(-1, 92)
    ax.set_xticks([0, 20, 40, 60, 80])
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    ax.text(90.5, -0.68, "$\Delta$", ha="right", va="bottom", fontsize=6.4)
    for yi, value in zip(y, lifts):
        if 0 < abs(value) < 0.1:
            delta_label = f"{value:+.3f}"
        elif 0 < abs(value) < 1:
            delta_label = f"{value:+.2f}"
        else:
            delta_label = f"{value:+.1f}"
        ax.text(
            90.5,
            yi,
            delta_label,
            va="center",
            ha="right",
            color=COLORS["navy"] if value > 0.05 else COLORS["coral"] if value < -0.05 else COLORS["slate"],
            fontsize=6.1,
        )


def plot_attribute_lifts() -> None:
    ordered = sorted(ATTRIBUTE_RESULTS, key=lambda row: row[3], reverse=True)
    amplified = [row for row in ordered if row[3] > 0.05]
    other = [row for row in ordered if row[3] <= 0.05]
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(6.5, 2.7),
        layout="constrained",
        gridspec_kw={"width_ratios": [1.0, 1.0], "wspace": 0.08},
    )
    attribute_panel(axes[0], amplified)
    attribute_panel(axes[1], other)
    panel_title(axes[0], "a", "Positive trace lift")
    panel_title(axes[1], "b", "Near-zero or negative trace lift")
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="outside upper center",
        ncol=2,
        frameon=False,
        handletextpad=0.35,
        columnspacing=1.0,
    )
    fig.supxlabel("Semantic ASR (%)", fontsize=7.0)
    save_figure(fig, "attribute_lifts")


def plot_channel_ablation(
    channel_rows: list[dict[str, Any]], channel_effect_rows: list[dict[str, Any]]
) -> None:
    labels = ["Schema\nonly", "Metadata\n+ sequence", "+ arguments", "+ results", "Full\ncatalog"]
    values = [row["asr_pct"] for row in channel_rows]
    colors = [COLORS["gray"], COLORS["slate"], COLORS["amber"], COLORS["teal"], COLORS["navy"]]

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(6.5, 1.85),
        layout="constrained",
        gridspec_kw={"width_ratios": [1.42, 1.0], "wspace": 0.20},
    )
    ax = axes[0]
    x = np.arange(len(values))
    bars = ax.bar(x, values, width=0.62, color=colors, edgecolor="white", linewidth=0.35)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Semantic ASR (%)")
    ax.set_ylim(0, 31.5)
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    panel_title(ax, "a", "Observation views")
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.55,
            f"{value:.1f}",
            ha="center",
            va="bottom",
            fontsize=6.8,
        )

    effect_labels = ["Arguments $-$ M", "Results $-$ M", "Interaction"]
    effect_colors = [COLORS["amber"], COLORS["teal"], COLORS["coral"]]
    effect_values = np.array([row["delta_pp"] for row in channel_effect_rows])
    effect_lower = np.array([row["ci95_low"] for row in channel_effect_rows])
    effect_upper = np.array([row["ci95_high"] for row in channel_effect_rows])
    y = np.arange(len(effect_labels))[::-1]
    ax = axes[1]
    ax.axvline(0, color=COLORS["slate"], linewidth=0.65, zorder=0)
    for yi, value, low, high, color in zip(y, effect_values, effect_lower, effect_upper, effect_colors):
        ax.barh(
            yi,
            value,
            height=0.42,
            color=color,
            edgecolor="white",
            linewidth=0.25,
            xerr=[[value - low], [high - value]],
            error_kw={"ecolor": COLORS["ink"], "elinewidth": 0.7, "capsize": 1.8, "capthick": 0.7},
        )
        ax.text(
            high + 0.65,
            yi,
            f"{value:+.2f}",
            ha="left",
            va="center",
            fontsize=6.8,
        )
    ax.set_yticks(y, effect_labels)
    ax.set_xlim(-14.8, 16.2)
    ax.set_xlabel("Paired difference (pp)")
    panel_title(ax, "b", "Channel effects (95% CI)")
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    save_figure(fig, "channel_ablation")


def forest_panel(
    ax: plt.Axes,
    labels: list[str],
    values: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    title: tuple[str, str],
    color: str = COLORS["navy"],
) -> None:
    y = np.arange(len(labels))[::-1]
    errors = np.vstack((values - lower, upper - values))
    ax.barh(
        y,
        values,
        height=0.44,
        color=color,
        edgecolor="white",
        linewidth=0.25,
        xerr=errors,
        error_kw={"ecolor": COLORS["ink"], "elinewidth": 0.7, "capsize": 1.8, "capthick": 0.7},
    )
    ax.axvline(0, color=COLORS["slate"], linewidth=0.65)
    ax.set_yticks(y, labels)
    panel_title(ax, title[0], title[1])
    ax.set_xlabel("Paired ASR difference (pp)")
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    span = max(upper) - min(lower)
    for yi, value, upper_bound in zip(y, values, upper):
        ax.text(upper_bound + 0.045 * span, yi, f"{value:+.2f}", va="center", fontsize=6.8)


def plot_aggregation_effects() -> None:
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(6.5, 1.75),
        layout="constrained",
        gridspec_kw={"width_ratios": [1.0, 1.12], "wspace": 0.20},
    )
    domain_values = np.array([row[1] for row in DOMAIN_RESULTS])
    domain_lower = np.array([row[2] for row in DOMAIN_RESULTS])
    domain_upper = np.array([row[3] for row in DOMAIN_RESULTS])
    forest_panel(
        axes[0],
        [row[0] for row in DOMAIN_RESULTS],
        domain_values,
        domain_lower,
        domain_upper,
        ("a", "By task domain"),
        COLORS["navy"],
    )
    axes[0].set_xlim(0, 16.2)

    scope = [
        ("Single server $-$ schema", 6.235, 5.807, 6.667),
        ("All servers $-$ single", 3.144, 2.875, 3.413),
        ("Four domains $-$ one domain", 6.512, 5.603, 7.338),
    ]
    forest_panel(
        axes[1],
        [row[0] for row in scope],
        np.array([row[1] for row in scope]),
        np.array([row[2] for row in scope]),
        np.array([row[3] for row in scope]),
        ("b", "By observer scope"),
        COLORS["teal"],
    )
    axes[1].set_xlim(0, 8.25)
    save_figure(fig, "aggregation_effects")


def plot_appendix_diagnostics(model_rows: list[dict[str, Any]]) -> None:
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(6.5, 1.95),
        layout="constrained",
        gridspec_kw={"width_ratios": [1.08, 1.0], "wspace": 0.20},
    )

    labels = [row[0] for row in CALL_COUNT_RESULTS]
    lifts = np.array([row[1] for row in CALL_COUNT_RESULTS])
    views = [row[2] for row in CALL_COUNT_RESULTS]
    x = np.arange(len(labels))
    bar_colors = [COLORS["coral"] if value < 0 else COLORS["navy"] for value in lifts]
    axes[0].bar(x, lifts, width=0.62, color=bar_colors, edgecolor="white", linewidth=0.25)
    axes[0].axhline(0, color=COLORS["slate"], linewidth=0.65)
    tick_labels = [f"{label.replace('--', '–')}\n$n$={count:,}" for label, count in zip(labels, views)]
    axes[0].set_xticks(x, tick_labels)
    axes[0].set_ylabel("Catalog $-$ paired schema (pp)")
    axes[0].set_xlabel("Visible calls")
    panel_title(axes[0], "a", "Call-count strata")
    axes[0].yaxis.grid(True)
    axes[0].set_ylim(-6.2, 13.0)
    for xi, value in zip(x, lifts):
        axes[0].text(
            xi,
            value + (0.55 if value >= 0 else -0.65),
            f"{value:+.1f}",
            ha="center",
            va="bottom" if value >= 0 else "top",
            fontsize=6.6,
        )

    y = np.arange(len(model_rows))[::-1]
    values = np.array([row["asr_pct"] for row in model_rows])
    lower = np.array([row["ci95_low"] for row in model_rows])
    upper = np.array([row["ci95_high"] for row in model_rows])
    axes[1].barh(
        y,
        values,
        height=0.44,
        color=COLORS["teal"],
        edgecolor="white",
        linewidth=0.25,
        xerr=np.vstack((values - lower, upper - values)),
        error_kw={"ecolor": COLORS["ink"], "elinewidth": 0.7, "capsize": 1.8, "capthick": 0.7},
    )
    short_names = {
        "DeepSeek-V4-Flash-0731": "DeepSeek-V4-Flash",
        "Gemini-3-Flash-Preview": "Gemini-3-Flash",
    }
    axes[1].set_yticks(y, [short_names.get(row["model"], row["model"]) for row in model_rows])
    axes[1].set_xlabel("Semantic ASR (%)")
    panel_title(axes[1], "b", "Attack-model replication")
    axes[1].xaxis.grid(True)
    axes[1].set_xlim(0, 31)
    axes[1].set_xticks([0, 10, 20, 30])
    for yi, value in zip(y, values):
        axes[1].text(value + 0.18, yi, f"{value:.1f}", va="center", fontsize=6.8)
    save_figure(fig, "appendix_diagnostics")


def main(input_root: Path = REPO_ROOT, output_root: Path = REPO_ROOT / "results/paper") -> None:
    configure_paths(input_root, output_root)
    PAPER_DATA.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    analysis = load_json(MAIN_ANALYSIS)
    posthoc = load_json(POSTHOC_AUDIT)
    channel = load_json(CHANNEL_ANALYSIS)
    model_generalization = load_json(MODEL_GENERALIZATION)
    profiles = load_json(PROFILE_REPORT)
    scenarios = load_json(SCENARIO_AUDIT)
    trajectories = load_json(TRAJECTORY_AUDIT)

    # Guard the handoff between frozen summaries and final paper numbers.
    assert analysis["arms"]["catalog_one_shot"]["correct"] == 17856
    assert analysis["arms"]["single_server"]["correct"] == 43871
    assert posthoc["corrected_main"]["catalog_one_shot"]["correct"] == 17852
    assert posthoc["corrected_main"]["single_server"]["correct"] == 43862
    assert channel["arms"]["metadata_sequence_parameters"]["overall"]["correct"] == 18844
    assert posthoc["corrected_channel"]["metadata_sequence_parameters"]["correct"] == 18840

    main_rows = []
    for key, label in [
        ("schema_prior", "Schema-only prior"),
        ("catalog_one_shot", "Full catalog"),
        ("raw_one_shot", "Raw repeated trace"),
        ("catalog_two_stage_final", "Two-stage final"),
        ("cross_domain_all_servers", "Four-domain aggregate"),
        ("single_server", "Single-server micro-average"),
    ]:
        row = posthoc["corrected_main"][key]
        main_rows.append({"arm": label, **row})
    write_csv(PAPER_DATA / "main_results.csv", main_rows)

    attribute_rows = [
        {
            "attribute": name,
            "schema_asr_pct": schema,
            "catalog_correct": correct,
            "catalog_slots": 4000,
            "catalog_asr_pct": catalog,
            "lift_pp": lift,
        }
        for name, schema, catalog, lift, correct in ATTRIBUTE_RESULTS
    ]
    write_csv(PAPER_DATA / "attribute_results.csv", attribute_rows)

    corrected_channel = posthoc["corrected_channel"]
    channel_rows = []
    for key, label in [
        ("schema_prior", "Schema-only prior"),
        ("metadata_sequence", "Metadata + sequence"),
        ("metadata_sequence_parameters", "Metadata + sequence + parameters"),
        ("metadata_sequence_results", "Metadata + sequence + results"),
        ("catalog_one_shot", "Full catalog"),
    ]:
        source = (
            posthoc["corrected_main"]["schema_prior"]
            if key == "schema_prior"
            else corrected_channel[key]
        )
        channel_rows.append({"view": label, **source})
    write_csv(PAPER_DATA / "channel_results.csv", channel_rows)

    channel_effect_rows = []
    for key, label in [
        ("parameter_increment", "Parameters over metadata"),
        ("result_increment", "Results over metadata"),
        ("parameter_result_interaction", "Parameter-result interaction"),
    ]:
        row = corrected_channel[key]
        channel_effect_rows.append(
            {
                "effect": label,
                "delta_pp": row["delta_pp"],
                "ci95_low": row["ci95_pp"][0],
                "ci95_high": row["ci95_pp"][1],
                "bootstrap_samples": row["bootstrap_samples"],
            }
        )
    write_csv(PAPER_DATA / "channel_effects.csv", channel_effect_rows)

    domain_rows = [
        {
            "domain": label,
            "catalog_asr_pct": asr,
            "lift_pp": lift,
            "ci95_low": low,
            "ci95_high": high,
        }
        for label, lift, low, high, asr in DOMAIN_RESULTS
    ]
    write_csv(PAPER_DATA / "domain_results.csv", domain_rows)

    scope_rows = [
        {
            "contrast": "Single server vs paired schema",
            "delta_pp": 6.235,
            "ci95_low": 5.807,
            "ci95_high": 6.667,
            "unit": "profile-balanced",
        },
        {
            "contrast": "All servers vs single server",
            "delta_pp": 3.144,
            "ci95_low": 2.875,
            "ci95_high": 3.413,
            "unit": "profile-balanced",
        },
        {
            "contrast": "Four domains vs single-domain catalog",
            "delta_pp": 6.512,
            "ci95_low": 5.603,
            "ci95_high": 7.338,
            "unit": "profile",
        },
    ]
    write_csv(PAPER_DATA / "scope_effects.csv", scope_rows)

    write_csv(
        PAPER_DATA / "call_count_results.csv",
        [
            {"visible_calls": label, "lift_pp": lift, "view_count": views}
            for label, lift, views in CALL_COUNT_RESULTS
        ],
    )

    false_positives = unicode_false_positives_by_server()
    assert sum(false_positives.values()) == posthoc["main_false_positive_counts_by_arm"]["single_server"]
    server_rows = []
    for source in analysis["single_server"]["servers"]:
        views = source["view_count"]
        slots = views * 17
        correction = false_positives[source["server_name"]]
        frozen_correct = round(source["asr_pct"] / 100 * slots)
        corrected_correct = frozen_correct - correction
        corrected_asr = corrected_correct / slots * 100
        corrected_lift = source["lift_vs_schema_pp"] - correction / slots * 100
        server_rows.append(
            {
                "server": source["server_name"],
                "views": views,
                "slots": slots,
                "correct": corrected_correct,
                "unicode_false_positives_removed": correction,
                "asr_pct": round(corrected_asr, 3),
                "lift_vs_paired_schema_pp": round(corrected_lift, 3),
            }
        )
    server_rows.sort(key=lambda row: row["asr_pct"], reverse=True)
    assert sum(row["correct"] for row in server_rows) == posthoc["corrected_main"]["single_server"]["correct"]
    write_csv(PAPER_DATA / "server_results.csv", server_rows)

    two_stage = posthoc["two_stage_observed_comparisons"]
    two_stage_rows = [
        {
            "measure": "Stage 1 inferred-only",
            **{k: two_stage["stage1_inferred_only"].get(k, "") for k in ["correct", "slots", "asr_pct", "coverage_slots", "precision_pct"]},
        },
        {
            "measure": "Stage 2 on Stage 1-unresolved slots",
            **{k: two_stage["stage2_on_stage1_unresolved"].get(k, "") for k in ["correct", "slots", "asr_pct"]},
            "coverage_slots": "",
            "precision_pct": "",
        },
        {
            "measure": "Schema prior on same unresolved slots",
            **{k: two_stage["schema_prior_on_same_unresolved_subset"].get(k, "") for k in ["correct", "slots", "asr_pct"]},
            "coverage_slots": "",
            "precision_pct": "",
        },
        {
            "measure": "Two-stage final",
            **{k: two_stage["two_stage_final"].get(k, "") for k in ["correct", "slots", "asr_pct"]},
            "coverage_slots": "",
            "precision_pct": "",
        },
    ]
    write_csv(PAPER_DATA / "two_stage_results.csv", two_stage_rows)
    write_csv(PAPER_DATA / "representation_results.csv", REPRESENTATION_RESULTS)

    model_rows = corrected_generalization_rows(model_generalization)
    write_csv(PAPER_DATA / "model_generalization.csv", model_rows)

    scale_rows = [
        {"stage": "Synthetic profiles", "count": profiles["requested_count"], "unit": "profiles"},
        {"stage": "Structured candidates", "count": profiles["candidate_count"], "unit": "candidates"},
        {"stage": "Validated candidate pool", "count": profiles["candidate_pool_size"], "unit": "candidates"},
        {"stage": "Scenarios", "count": scenarios["record_count"], "unit": "tasks"},
        {"stage": "Tool trajectories", "count": trajectories["record_count"], "unit": "trajectories"},
        {"stage": "Recorded tool events", "count": trajectories["tool_record_count"], "unit": "events"},
        {"stage": "MCP executions", "count": trajectories["mcp_executed_record_count"], "unit": "executions"},
        {"stage": "Cache hits", "count": trajectories["cache_hit_record_count"], "unit": "events"},
        {"stage": "Distinct planned tools", "count": scenarios["distinct_tool_count"], "unit": "tools"},
        {"stage": "Tool servers", "count": len(analysis["single_server"]["servers"]), "unit": "servers"},
    ]
    write_csv(PAPER_DATA / "dataset_scale.csv", scale_rows)

    manifest = {
        "schema_version": "1.0",
        "paper_result_version": "posthoc-unicode-corrected",
        "source_of_truth": "Unicode audit plus final experiment report; frozen JSON is validated and used for unaffected fields.",
        "sources": [
            str(path.relative_to(ROOT))
            for path in [
                MAIN_ANALYSIS,
                POSTHOC_AUDIT,
                CHANNEL_ANALYSIS,
                ATTACK_RECORDS,
                MODEL_GENERALIZATION,
                PROFILE_REPORT,
                SCENARIO_AUDIT,
                TRAJECTORY_AUDIT,
                FINAL_REPORT,
            ]
            + [path for pair in GENERALIZATION_RUNS.values() for path in pair]
        ],
        "dataset": {
            "profiles": profiles["requested_count"],
            "profile_attributes": 17,
            "countries": 26,
            "scenarios": scenarios["record_count"],
            "domains": scenarios["domain_counts"],
            "trajectories": trajectories["record_count"],
            "tool_records": trajectories["tool_record_count"],
            "mcp_executions": trajectories["mcp_executed_record_count"],
            "cache_hits": trajectories["cache_hit_record_count"],
            "servers": len(server_rows),
        },
        "main_results": main_rows,
        "channel_results": channel_rows,
        "channel_effects": channel_effect_rows,
        "domain_results": domain_rows,
        "scope_effects": scope_rows,
        "pii_catalog": posthoc["corrected_pii_catalog"],
        "representation_results": REPRESENTATION_RESULTS,
        "two_stage": two_stage,
        "model_generalization": model_rows,
        "statistical_protocol": {
            "main_bootstrap_samples": 5000,
            "channel_bootstrap_samples": 10000,
            "cluster": "profile_id",
            "interval": "95% percentile bootstrap",
            "failed_or_missing_predictions": "incorrect and retained in the denominator",
        },
    }
    (PAPER_DATA / "experimental_summary.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    configure_plotting()
    plot_attribute_lifts()
    plot_channel_ablation(channel_rows, channel_effect_rows)
    plot_aggregation_effects()
    plot_appendix_diagnostics(model_rows)

    print(f"Wrote {len(list(PAPER_DATA.iterdir()))} data files to {PAPER_DATA}")
    print(f"Wrote {len(list(FIGURES.iterdir()))} figure files to {FIGURES}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-root",
        type=Path,
        default=REPO_ROOT,
        help="directory containing artifacts/ (default: repository root)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "results/paper",
        help="directory that receives data/ and figures/",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    main(arguments.input_root, arguments.output_root)
