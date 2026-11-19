#!/usr/bin/env python3
"""Plot content-review labels against strict verifier results."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import PercentFormatter


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "report_materials"
DATA = OUTPUT / "content_review_all_260.json"


def main() -> None:
    """Render a normalized comparison with counts and explicit denominators."""
    data = json.loads(DATA.read_text(encoding="utf-8"))
    labels = ("complete", "partial", "incomplete")
    cells = data["vs_strict"]
    rows = []
    for label in labels:
        row = cells[label]
        total = row["n"]
        assert total == row["strict_pass"] + row["strict_fail"]
        assert row["no_score"] == 0
        rows.append((label, row["strict_pass"], row["strict_fail"], total))
    missing = cells["cannot_judge"]
    assert missing["n"] == missing["no_score"]
    assert sum(row[3] for row in rows) + missing["n"] == data["n"]

    pass_color, fail_color = "#0072B2", "#E69F00"
    plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(8.2, 4.2))
    fig.subplots_adjust(left=0.25, right=0.97, bottom=0.27, top=0.72)
    for index, (label, passed, failed, total) in enumerate(rows):
        pass_pct, fail_pct = 100 * passed / total, 100 * failed / total
        ax.barh(index, pass_pct, color=pass_color, height=0.55)
        ax.barh(index, fail_pct, left=pass_pct, color=fail_color, height=0.55)
        ax.text(pass_pct / 2, index, f"{passed:,}  ({pass_pct:.1f}%)",
                ha="center", va="center", color="white", fontsize=10, weight="bold")
        ax.text(pass_pct + fail_pct / 2, index, f"{failed:,}  ({fail_pct:.1f}%)",
                ha="center", va="center", color="#202020", fontsize=10, weight="bold")
    ax.set_yticks(range(len(rows)), [f"{name.title()}  (n = {total:,})"
                                    for name, _, _, total in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    ax.set_xlabel("Share within each content label")
    fig.text(0.03, 0.96, "Content completion and strict verification",
             fontsize=15, weight="bold", va="top")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="y", length=0, pad=10)
    ax.tick_params(axis="x", colors="#555555")
    fig.legend(handles=[Patch(color=pass_color, label="Strict pass"),
                        Patch(color=fail_color, label="Strict fail")],
               loc="upper left", bbox_to_anchor=(0.03, 0.86), ncol=2,
               frameon=False, fontsize=9)
    fig.text(0.03, 0.025,
             f"Counts shown inside bars; {missing['n']} identities without a response are excluded.\n"
             "2,080 identities: 260 prompts × 2 models × 4 conditions; answers are paired.",
             fontsize=8.5, color="#555555", va="bottom")
    for extension in ("png", "svg"):
        fig.savefig(OUTPUT / f"04_content_crosscheck_v2.{extension}", dpi=220,
                    bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
