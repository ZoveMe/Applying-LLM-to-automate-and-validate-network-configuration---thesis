#!/usr/bin/env python3
"""Generate docs/figures/week6-explain/explain-quality.svg from explain-evaluation.json.

Same visual style as docs/figures/week5-v2/model-quality.svg (colors, fonts,
layout), so Chapter 6 figures stay consistent. Regenerable from preserved
evidence — never hand-edited.

Usage:
    python3 docs/tools/generate_explain_figure.py \
        [--evaluation docs/evidence/week6-explain/explain-evaluation.json] \
        [--out docs/figures/week6-explain/explain-quality.svg]
"""
import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

BLUE = "#2563eb"    # Qwen2.5-Coder 7B
AMBER = "#f59e0b"   # Qwen3 4B
FONT = "Arial, sans-serif"

METRICS = [
    ("Schema validity", "schema_validity"),
    ("Route recall", "mean_route_recall"),
    ("Route precision", "mean_route_precision"),
    ("Interface recall", "mean_iface_recall"),
    ("Interface precision", "mean_iface_precision"),
    ("Policy recall (r1)", "mean_policy_recall"),
]

X0, X1 = 330, 1010          # bar area (680 px = 100%)
ROW_PITCH, BAR_H = 66, 17
TOP = 140


def bar(y: float, value: float, color: str) -> str:
    width = round(value * (X1 - X0), 2)
    label_x = X0 + width + 7
    pct = f"{value * 100:.1f}%"
    parts = [f'<rect x="{X0}" y="{y}" width="{width}" height="{BAR_H}" rx="3" fill="{color}"/>']
    parts.append(
        f'<text x="{label_x}" y="{y + 13}" font-family="{FONT}" font-size="12" '
        f'font-weight="700" fill="#374151">{pct}</text>'
    )
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluation",
        default=str(REPO_ROOT / "docs/evidence/week6-explain/explain-evaluation.json"),
    )
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "docs/figures/week6-explain/explain-quality.svg"),
    )
    args = parser.parse_args()

    aggregate = json.loads(Path(args.evaluation).read_text())["aggregate"]
    models = sorted(aggregate)  # qwen2.5 before qwen3 alphabetically
    m1, m2 = aggregate[models[0]], aggregate[models[1]]
    runs = m1["runs"] + m2["runs"]

    chart_bottom = TOP + len(METRICS) * ROW_PITCH + 10
    height = chart_bottom + 62

    svg = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="{height}" '
        f'viewBox="0 0 1080 {height}" role="img" aria-labelledby="title description">',
        '<title id="title">EXPLAIN task: factual grounding</title>',
        f'<desc id="description">{runs} live Ollama runs; a higher percentage is better.</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="40" y="44" font-family="{FONT}" font-size="25" font-weight="700" '
        'fill="#111827">EXPLAIN task: factual grounding of explanations</text>',
        f'<text x="40" y="73" font-family="{FONT}" font-size="14" fill="#4b5563">'
        f'{runs} live Ollama runs; scored against intended state; higher is better.</text>',
        f'<rect x="40" y="97" width="18" height="18" rx="3" fill="{BLUE}"/>',
        f'<text x="67" y="111" font-family="{FONT}" font-size="14" fill="#1f2937">Qwen2.5-Coder 7B</text>',
        f'<rect x="290" y="97" width="18" height="18" rx="3" fill="{AMBER}"/>',
        f'<text x="317" y="111" font-family="{FONT}" font-size="14" fill="#1f2937">Qwen3 4B</text>',
    ]

    for i in range(6):
        x = X0 + i * (X1 - X0) / 5
        svg.append(
            f'<line x1="{x:.2f}" y1="{TOP}" x2="{x:.2f}" y2="{chart_bottom}" '
            'stroke="#e5e7eb" stroke-width="1"/>'
        )
        svg.append(
            f'<text x="{x:.2f}" y="{chart_bottom + 32}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="12" fill="#6b7280">{i * 20}%</text>'
        )

    for i, (label, key) in enumerate(METRICS):
        y1 = TOP + 17 + i * ROW_PITCH
        y2 = y1 + 25
        svg.append(
            f'<text x="315" y="{y1 + 20}" text-anchor="end" font-family="{FONT}" '
            f'font-size="14" fill="#1f2937">{label}</text>'
        )
        svg.append(bar(y1, m1[key] or 0.0, BLUE))
        svg.append(bar(y2, m2[key] or 0.0, AMBER))

    svg.append("</svg>")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(svg) + "\n")
    print(f"Figure -> {out_path}")


if __name__ == "__main__":
    main()
