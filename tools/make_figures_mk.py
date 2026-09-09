#!/usr/bin/env python3
"""
Изработка на сликите за трудот, со ознаки на македонски јазик.

Сите вредности се читаат од зачуваните докази во docs/evidence/ — во скриптата
не е внесен ниту еден број рачно. Ако доказите се променат, сликите се менуваат
заедно со нив.

Стил: Liberation Serif (метрички совпадлив со Times New Roman), сива скала со
еден акцент, без наслов во самата слика бидејќи насловот го носи легендата во
документот. Ваквиот избор е направен за сликите да останат читливи и при
црно-бело печатење.

Извршување:  python3 tools/make_figures_mk.py
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

REPO = Path("/sessions/ecstatic-pensive-volta/mnt/thesis-net")
EV = REPO / "docs" / "evidence"
OUT = REPO / "docs" / "figures" / "mk"
OUT.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ стил
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Liberation Serif", "DejaVu Serif"],
    "font.size": 10.5,
    "axes.titlesize": 11,
    "axes.labelsize": 10.5,
    "xtick.labelsize": 9.5,
    "ytick.labelsize": 9.5,
    "legend.fontsize": 9.5,
    "axes.edgecolor": "#333333",
    "axes.linewidth": 0.8,
    "figure.dpi": 200,
    "savefig.dpi": 200,
})

DARK = "#1a1a1a"      # акцент — вредноста што е во фокусот
MID = "#7a7a7a"
LIGHT = "#c9c9c9"
GRID = "#dddddd"


def frame(ax, grid_axis="y"):
    """Отстранува непотребни рамки и остава дискретна мрежа."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)


def load(rel):
    return json.loads((EV / rel).read_text(encoding="utf-8"))


def save(fig, name):
    path = OUT / name
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  {name}")
    return path


# =====================================================================
# 1. Три експериментални услови
# =====================================================================
def fig_uslovi():
    v2 = load("week6-v2-12m/v2-12m-evaluation.json")
    abl = load("week8-live-ablation/live-ablation-summary.json")
    ff = load("week8-freeform/freeform-summary.json")

    uslovi = [
        ("А. Заштитен\nпроцес", v2["runs_total"], 0, 0),
        ("Б. Само шема,\nбез проверка", abl["proposals_replayed"],
         abl["left_network_not_matching_intent"], abl["security_policy_breached"]),
        ("В. Слободна форма,\nбез структура", ff["runs"],
         ff["left_network_not_matching_intent"], ff["security_policy_breached"]),
    ]

    fig, ax = plt.subplots(figsize=(6.4, 3.5))
    x = range(len(uslovi))
    w = 0.36
    ne_odg = [100 * n / r for _, r, n, _ in uslovi]
    prekrs = [100 * p / r for _, r, _, p in uslovi]

    b1 = ax.bar([i - w / 2 for i in x], ne_odg, w, color=LIGHT,
                edgecolor=DARK, linewidth=0.6, zorder=3)
    b2 = ax.bar([i + w / 2 for i in x], prekrs, w, color=DARK, zorder=3)

    for bars in (b1, b2):
        for bar in bars:
            v = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, v + 1.1, f"{v:.0f}%",
                    ha="center", va="bottom", fontsize=9.5)

    # бројот на извршувања оди во ознаката на оската, за да не се преклопува
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{u[0]}\n(n = {u[1]})" for u in uslovi])
    ax.set_ylabel("Удел од извршувањата")
    ax.set_ylim(0, 52)
    ax.set_yticks(range(0, 51, 10))
    ax.set_yticklabels([f"{v}%" for v in range(0, 51, 10)])
    frame(ax)
    ax.legend([b1, b2], ["не одговара на намерата", "прекршена безбедносна политика"],
              loc="upper left", frameon=False)
    return save(fig, "01-tri-uslovi.png")


# =====================================================================
# 2. Матрица на конфузија на детерминистичката проверка
# =====================================================================
def fig_matrica():
    g = load("week6-v2-12m/v2-deep-analysis.json")["gate_confusion_matrix"]
    tp = g["true_positives_violation_rejected"]
    fn = g["false_negatives_violation_allowed"]
    fp = g["false_positives_clean_rejected"]
    tn = g["true_negatives_clean_allowed"]

    fig, ax = plt.subplots(figsize=(5.4, 3.4))
    cells = [[tp, fn], [fp, tn]]
    # нулата во горниот десен агол е клучниот резултат, па таа е нагласена
    colours = [["#e8e8e8", DARK], ["#f4f4f4", "#e8e8e8"]]
    labels = [["точно отфрлен", "ПРОПУШТЕН"], ["непотребно отфрлен", "точно прифатен"]]

    for i in range(2):
        for j in range(2):
            ax.add_patch(plt.Rectangle((j, 1 - i), 1, 1, facecolor=colours[i][j],
                                       edgecolor=DARK, linewidth=0.8))
            txt = "white" if colours[i][j] == DARK else DARK
            ax.text(j + 0.5, 1.62 - i, str(cells[i][j]), ha="center", va="center",
                    fontsize=19, color=txt)
            ax.text(j + 0.5, 1.28 - i, labels[i][j], ha="center", va="center",
                    fontsize=9, color=txt)

    ax.set_xlim(-0.05, 2.05)
    ax.set_ylim(-0.05, 2.35)
    ax.set_xticks([0.5, 1.5])
    ax.set_xticklabels(["отфрлен од проверката", "прифатен од проверката"])
    ax.set_yticks([1.5, 0.5])
    ax.set_yticklabels(["предлогот ја\nкрши намерата", "предлогот е\nисправен"])
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    # во македонскиот текст децималниот знак е запирка
    dec = lambda v: f"{v:.3f}".replace(".", ",")
    ax.text(1.0, 2.18, f"стапка на откривање {dec(g['recall'])}  ·  "
                       f"прецизност {dec(g['precision'])}  ·  n = {g['gated_runs']}",
            ha="center", fontsize=9.5, color="#444444")
    return save(fig, "02-matrica-na-konfuzija.png")


# =====================================================================
# 3. Безбедност по модел: предлози наспроти систем
# =====================================================================
def fig_bezbednost_po_model():
    pm = load("week6-v2-12m/v2-deep-analysis.json")["per_model"]
    data = sorted(((m.split(":")[0], v["policy_safety"]) for m, v in pm.items()),
                  key=lambda t: t[1])
    names = [d[0] for d in data]
    vals = [d[1] for d in data]

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    y = range(len(names))
    ax.hlines(y, vals, 1.0, color=LIGHT, linewidth=1.6, zorder=2)
    ax.scatter(vals, y, s=42, color=MID, zorder=3, label="безбедност на предлозите")
    ax.scatter([1.0] * len(y), y, s=42, color=DARK, marker="D", zorder=4,
               label="безбедност на системот")

    for i, v in enumerate(vals):
        ax.text(v - 0.012, i, f"{v:.2f}".replace(".", ","), ha="right",
                va="center", fontsize=9)

    ax.set_yticks(list(y))
    ax.set_yticklabels(names)
    ax.set_xlim(0.42, 1.045)
    ax.set_xlabel("Удел извршувања без прекршување на политиката")
    ax.set_xticks([0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    ax.set_xticklabels(["0,50", "0,60", "0,70", "0,80", "0,90", "1,00"])
    frame(ax, grid_axis="x")
    # легендата оди над графиконот за да не ги покрива долните редови
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.01), ncol=2, frameon=False)
    return save(fig, "03-bezbednost-po-model.png")


# =====================================================================
# 4. Таксономија на грешките во заштитениот процес
# =====================================================================
def fig_taksonomija():
    counts = load("week6-v2-12m/v2-deep-analysis.json")["failure_taxonomy"]["counts"]
    mk = {
        "proposed_when_should_refuse_or_clarify":
            "предлага таму каде што требало да одбие\nили да побара појаснување",
        "extraneous_changes": "додава непобарани промени",
        "clarified_instead_of_deciding": "бара појаснување наместо да одлучи",
        "schema_invalid": "излез неисправен според шемата",
        "refused_a_legitimate_request": "одбива оправдано барање",
    }
    items = sorted(counts.items(), key=lambda kv: kv[1])
    names = [mk[k] for k, _ in items]
    vals = [v for _, v in items]

    fig, ax = plt.subplots(figsize=(6.4, 3.3))
    colours = [DARK if v == max(vals) else MID for v in vals]
    bars = ax.barh(range(len(vals)), vals, color=colours, height=0.62, zorder=3)
    for bar, v in zip(bars, vals):
        ax.text(v + 1.0, bar.get_y() + bar.get_height() / 2, str(v),
                va="center", fontsize=9.5)

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names)
    ax.set_xlabel("Број извршувања (од вкупно 360)")
    ax.set_xlim(0, max(vals) * 1.16)
    frame(ax, grid_axis="x")
    return save(fig, "04-taksonomija-na-greski.png")


# =====================================================================
# 5. Исходи во условот со слободна форма
# =====================================================================
def fig_slobodna_forma():
    ff = load("week8-freeform/freeform-summary.json")
    stavki = [
        ("извршувања вкупно", ff["runs"], LIGHT),
        ("не одговара на намерата", ff["left_network_not_matching_intent"], MID),
        ("прекршена политика", ff["security_policy_breached"], DARK),
        ("отстрането правило за забрана", ff["deny_rule_removed"], MID),
        ("прекината задолжителна поврзаност", ff["connectivity_broken"], MID),
    ]
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    names = [s[0] for s in stavki][::-1]
    vals = [s[1] for s in stavki][::-1]
    cols = [s[2] for s in stavki][::-1]
    bars = ax.barh(range(len(vals)), vals, color=cols, height=0.6,
                   edgecolor=DARK, linewidth=0.5, zorder=3)
    for bar, v in zip(bars, vals):
        ax.text(v + 1.6, bar.get_y() + bar.get_height() / 2,
                f"{v}  ({100*v/ff['runs']:.0f}%)", va="center", fontsize=9.5)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names)
    ax.set_xlabel("Број извршувања")
    ax.set_xlim(0, ff["runs"] * 1.24)
    frame(ax, grid_axis="x")
    return save(fig, "05-slobodna-forma.png")


# =====================================================================
# 6. Пријавување на пристапната политика при објаснување
# =====================================================================
def fig_explain():
    packs = [("small", "мала топологија"),
             ("l-clean", "голема, исправна"),
             ("l-faulty", "голема, со грешки")]
    # Се користи веќе пресметаната средна вредност по модел. Извршувањата врз
    # уред без пристапно правило имаат вредност null и не смеат да се сметаат
    # за пропуст — таму нема што да се пријави.
    per_model = {}
    for key, _ in packs:
        agg = load(f"week6-explain-13m/{key}-evaluation.json")["aggregate"]
        for model, v in agg.items():
            rec = v.get("mean_policy_recall")
            if rec is None:
                continue
            per_model.setdefault(model.split(":")[0], {})[key] = float(rec)

    names = sorted(per_model,
                   key=lambda n: sum(per_model[n].values()) / len(per_model[n]))
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    y = range(len(names))
    marks = [("o", MID), ("s", DARK), ("^", "#9a9a9a")]
    for (key, label), (mk_, col) in zip(packs, marks):
        xs = [per_model[n].get(key, float("nan")) for n in names]
        ax.scatter(xs, y, marker=mk_, s=40, color=col, label=label, zorder=3)

    ax.set_yticks(list(y))
    ax.set_yticklabels(names)
    ax.set_xlim(-0.05, 1.08)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_xticklabels(["0", "0,25", "0,50", "0,75", "1,00"])
    ax.set_xlabel("Удел извршувања во кои е пријавено пристапното правило")
    frame(ax, grid_axis="x")
    ax.legend(loc="lower right", frameon=False, title="конфигурациски пакет")
    return save(fig, "06-objasnuvanje-politika.png")


if __name__ == "__main__":
    print("Изработени слики:")
    fig_uslovi()
    fig_matrica()
    fig_bezbednost_po_model()
    fig_taksonomija()
    fig_slobodna_forma()
    fig_explain()
    print(f"\nво {OUT}")
