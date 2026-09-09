#!/usr/bin/env python3
"""
Correct four numeric claims that disagree with the preserved evidence.

Each correction below was derived by recomputing the figure from the evidence
JSON in docs/evidence/, not by re-reading the prose. The check that produced
them is tools/verify_thesis_claims.py, which fails if the document and the
evidence drift apart again.
"""
import re
import sys
from pathlib import Path

from docx import Document

SRC = Path("/sessions/ecstatic-pensive-volta/mnt/thesis-net/docs/"
           "Дипломска-работа-Митровски-222022.docx")

# (why, old fragment, new fragment)
CORRECTIONS = [
    (
        "CLAIM-009: twelve models drawn from eight families, not twelve families",
        "потврден преку 360 извршувања и дванаесет различни фамилии модели.",
        "потврден преку 360 извршувања со дванаесет модели од осум различни фамилии.",
    ),
    (
        "CLAIM-007: T9 covers 8 distinct models from 6 families, not 7 families",
        "при што биле опфатени модели од седум различни фамилии.",
        "при што биле опфатени осум различни модели од шест фамилии.",
    ),
    (
        "CLAIM-006: rule removed in 8 runs; realised policy breach in 6 of them",
        "Во шест случаи била отстранета заштитата што ја блокира комуникацијата "
        "од клиентскиот кон менаџмент сегментот, додека во 24 случаи била "
        "прекината задолжителната мрежна достапност.",
        "Во осум случаи било отстрането правилото што ја блокира комуникацијата "
        "од клиентскиот кон менаџмент сегментот, при што само во шест од нив тоа "
        "доведе до вистинско прекршување на политиката — разлика што е објаснета "
        "подолу во овој дел. Во 24 случаи била прекината задолжителната мрежна "
        "достапност.",
    ),
    (
        "CLAIM-008: reset records show 2 unrestored runs, not 4",
        "туку тоа што четири извршувања ја оставија лабораториската околина во "
        "состојба што не можеше да се поправи со ниту една реконфигурациска "
        "команда, поради што беше потребно целосно повторно поставување на "
        "топологијата.",
        "туку тоа што две извршувања ја оставија лабораториската околина во "
        "состојба што автоматската постапка за враќање не успеа да ја поправи, "
        "поради што беше потребно целосно повторно поставување на топологијата.",
    ),
]


def normalise(s):
    return re.sub(r"\s+", " ", s).strip()


def main():
    doc = Document(str(SRC))
    applied, missed = [], []

    for why, old, new in CORRECTIONS:
        old_n = normalise(old)
        hit = False
        for p in doc.paragraphs:
            if old_n not in normalise(p.text):
                continue
            # single-run paragraphs are the norm here; anything else would risk
            # dropping inline formatting, so refuse rather than flatten it
            if len(p.runs) != 1:
                raise RuntimeError(
                    f"paragraph for {why} has {len(p.runs)} runs; edit it by hand"
                )
            p.runs[0].text = normalise(p.runs[0].text).replace(old_n, normalise(new))
            applied.append(why)
            hit = True
            break
        if not hit:
            missed.append(why)

    if missed:
        print("NOT APPLIED:")
        for m in missed:
            print("  -", m)
        return 1

    doc.save(str(SRC))
    for a in applied:
        print("applied:", a)
    print(f"\n{len(applied)}/{len(CORRECTIONS)} corrections written to {SRC.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
