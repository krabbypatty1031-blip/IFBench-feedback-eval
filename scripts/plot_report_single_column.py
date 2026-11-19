#!/usr/bin/env python3
"""Create compact vector figures from the saved report results."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "outputs/report_materials/results.json"
OUT = ROOT / "report/figures"
MODELS = ("gpt-4.1", "gpt-4o")
CONDITIONS = ("B1", "B2", "B3")
BLUE, ORANGE, GREEN, PURPLE = "#0072B2", "#E69F00", "#009E73", "#CC79A7"


def save(fig, name):
    """Save a report-ready vector graphic and a high-resolution preview."""
    for extension in ("pdf", "png"):
        fig.savefig(OUT / f"{name}.{extension}", dpi=300, facecolor="white")
    plt.close(fig)


def paired_effects(data):
    """Plot paired strict differences and the saved bootstrap intervals."""
    fig, axes = plt.subplots(2, 1, figsize=(3.45, 3.75), sharex=True)
    fig.subplots_adjust(left=0.23, right=0.97, top=0.93, bottom=0.12, hspace=0.40)
    for ax, model in zip(axes, MODELS):
        rows = [row for row in data["contrasts"] if row["model"] == model]
        assert len(rows) == 5 and all(row["n_pairs"] == 259 for row in rows)
        for i, row in enumerate(rows):
            point = 100 * row["point"]
            ax.errorbar(point, i, xerr=[[point - 100 * row["low"]],
                                       [100 * row["high"] - point]],
                        fmt="o", markersize=3.5, capsize=2,
                        color=PURPLE if row["exploratory"] else BLUE)
        ax.set_yticks(range(5), [row["contrast"] + (" *" if row["exploratory"] else "")
                                  for row in rows])
        ax.invert_yaxis()
        ax.axvline(0, color="0.55", linestyle="--", linewidth=0.7)
        ax.axhline(2.5, color="0.85", linewidth=0.6)
        ax.set_xlim(-4, 21)
        ax.set_title(f"{model}  (n = 259)", fontsize=9, pad=3)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(length=2, pad=2)
    axes[-1].set_xlabel("Paired strict difference (percentage points)", labelpad=3)
    save(fig, "02_paired_effects_single")


def repairs(data):
    """Plot repairs and regressions using their separate eligible denominators."""
    fig, ax = plt.subplots(figsize=(3.45, 2.75))
    fig.subplots_adjust(left=0.21, right=0.98, top=0.84, bottom=0.18)
    positions = np.array([0, 1, 2, 3.7, 4.7, 5.7])
    for offset, metric, color, label in ((-0.18, "repair", GREEN, "Repair"),
                                         (0.18, "regress", PURPLE, "Regression")):
        rows = [data["summary"]["by_model"][model][metric][condition]
                for model in MODELS for condition in CONDITIONS]
        bars = ax.bar(positions + offset, [100 * row["value"] for row in rows],
                      width=0.34, label=label, color=color)
        ax.bar_label(bars, labels=[f"{row['n']}/{row['d']}" for row in rows],
                     fontsize=6.5, padding=1.5, rotation=90)
    ax.set_xticks(positions, CONDITIONS * 2)
    ax.set_ylim(0, 48)
    ax.set_ylabel("Rate (%)")
    ax.text(1, 45.5, MODELS[0], ha="center", va="center", fontsize=8)
    ax.text(4.7, 45.5, MODELS[1], ha="center", va="center", fontsize=8)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.18), ncol=2,
              frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.15)
    ax.set_axisbelow(True)
    save(fig, "03_repair_regression_single")


def content_crosscheck(data):
    """Plot strict outcomes as percentages of each reviewed content category."""
    cross = data["content_vs_strict"]
    labels = ("complete", "partial", "incomplete")
    fig, ax = plt.subplots(figsize=(3.45, 1.95))
    fig.subplots_adjust(left=0.28, right=0.94, top=0.77, bottom=0.24)
    for i, label in enumerate(labels):
        passed, failed = cross[label]["strict_pass"], cross[label]["strict_fail"]
        total = passed + failed
        percent = 100 * passed / total
        ax.barh(i, percent, height=0.55, color=BLUE)
        ax.barh(i, 100 - percent, left=percent, height=0.55, color=ORANGE)
        ax.text(percent / 2, i, f"{passed:,}", ha="center", va="center",
                fontsize=8, color="white", weight="bold")
        ax.text((100 + percent) / 2, i, f"{failed:,}", ha="center", va="center",
                fontsize=8, color="black", weight="bold")
    ax.set_yticks(range(3), [label.title() for label in labels])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xticks([0, 25, 50, 75, 100], ["0", "25", "50", "75", "100"])
    ax.set_xlabel("Share within content category (%)", labelpad=2)
    ax.tick_params(axis="y", length=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(handles=[Patch(color=BLUE, label="Strict pass"),
                       Patch(color=ORANGE, label="Strict fail")],
              loc="upper center", bbox_to_anchor=(0.5, 1.37), ncol=2,
              frameon=False, fontsize=8)
    save(fig, "04_content_crosscheck_single")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(DATA.read_text(encoding="utf-8"))
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8,
                         "pdf.fonttype": 42, "axes.linewidth": 0.6})
    paired_effects(data)
    repairs(data)
    content_crosscheck(data)


if __name__ == "__main__":
    main()
