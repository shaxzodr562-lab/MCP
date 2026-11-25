"""Decide, per sub-annual indicator, whether values are cumulative within the year.

Evidence (pooled over all members of an indicator):
  within_score : share of consecutive steps *inside* a year with value_next >= value_prev
  reset_score  : share of year boundaries (Q4->Q1 or M12->M01) where the new year's first value is
                 *lower* than the previous year's last value (a cumulative series restarts from ~0)

  ratio_cum / ratio_level : within a year, last/first value is ~k for a cumulative
                 series (Q4/Q1 ~ 4, M12/M01 ~ 12) but ~1 for a level; share of member-years >= 0.6k / <= 0.2k+0.8 (k = periods present, min 3 quarters / 6 months)

  cumulative  : within >= 0.9, no 'level' vote, and at least one other 'cumulative' vote
                (reset >= 0.7 with >=1 boundary, or ratio_cum >= 0.7)
  level/stock : any 'level' vote (reset <= 0.3, ratio_level >= 0.7, within < 0.6) and no 'cumulative' vote
  unit is a rate/index/share -> never cumulative, regardless of the numbers
  anything else -> NULL (needs human review, listed in data/cumulative_review.csv)
"""
from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

from .config import settings

RATE_WORDS = ("foiz", "%", "indeks", "marta", "barobar", "nisbatan", "ulush", "ball", "koeffitsient")
MIN_WITHIN = 3


def _is_rate_unit(unit: str | None, name: str | None) -> bool:
    text = f"{unit or ''} {name or ''}".lower()
    return any(w in text for w in RATE_WORDS)


def _evidence(db: sqlite3.Connection, ind_id: int):
    rows = db.execute(
        """SELECT o.member_id, p.year, p.sort_key, p.kind, o.value
           FROM observations o JOIN periods p ON p.id = o.period_id
           WHERE o.indicator_id = ? AND p.kind IN ('quarter','month') AND o.value IS NOT NULL
           ORDER BY o.member_id, p.sort_key""",
        (ind_id,),
    ).fetchall()
    by_member = defaultdict(list)
    for m, y, sk, kind, v in rows:
        by_member[m].append((y, sk, kind, v))
    w_ok = w_n = b_drop = b_n = 0
    r_n = r_cum = r_lvl = 0
    for series in by_member.values():
        years = defaultdict(list)
        for y, sk, kind, v in series:
            years[(y, kind)].append(v)
        for (y, kind), vals in years.items():
            k = len(vals)
            if k >= (3 if kind == "quarter" else 6) and vals[0] > 0 and vals[-1] > 0:
                ratio = vals[-1] / vals[0]
                r_n += 1
                r_cum += ratio >= 0.6 * k          # cumulative: last/first ~ k
                r_lvl += ratio <= 0.2 * k + 0.8    # level: last/first ~ 1 (+ growth)
        for (y0, sk0, k0, v0), (y1, sk1, k1, v1) in zip(series, series[1:]):
            if k0 != k1 or (v0 == 0 and v1 == 0):
                continue
            step = sk1 - sk0
            if y1 == y0:
                if step <= 3:  # adjacent periods only (no gaps)
                    w_n += 1
                    w_ok += v1 >= v0
            elif y1 == y0 + 1:
                gap_ok = (k0 == "quarter" and sk0 % 100 == 12 and sk1 % 100 == 3) or (
                    k0 == "month" and sk0 % 100 == 12 and sk1 % 100 == 1
                )
                if gap_ok:
                    b_n += 1
                    b_drop += v1 < v0
    return (
        w_n, (w_ok / w_n if w_n else None),
        b_n, (b_drop / b_n if b_n else None),
        r_n, (r_cum / r_n if r_n else None), (r_lvl / r_n if r_n else None),
    )


OVERRIDES = Path(__file__).parent / "cumulative_overrides.csv"


def _apply_overrides(db: sqlite3.Connection, review: list) -> None:
    """Manual decisions (versioned in cumulative_overrides.csv) win over the heuristics."""
    if not OVERRIDES.exists():
        return
    done = set()
    with OVERRIDES.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            db.execute(
                "UPDATE indicators SET is_cumulative=?, cumulative_basis=? WHERE id=?",
                (int(row["is_cumulative"]), "manual: " + row["reason"], int(row["id"])),
            )
            done.add(int(row["id"]))
    review[:] = [r for r in review if r[0] not in done]


def classify_all(db: sqlite3.Connection) -> dict:
    review, counts = [], defaultdict(int)
    for ind_id, per, unit, name in db.execute(
        "SELECT id, periodicity, unit_uz, name_uz FROM indicators"
    ).fetchall():
        if per not in ("Choraklik", "Oylik"):
            flag, basis, w, r = None, "not_subannual", None, None
        elif _is_rate_unit(unit, name):
            _, w, _, r, *_ = _evidence(db, ind_id)
            flag, basis = 0, "rate_unit"
        else:
            w_n, w, b_n, r, r_n, rc, rl = _evidence(db, ind_id)
            cum_votes = []
            lvl_votes = []
            if b_n >= 1 and r is not None:
                (cum_votes if r >= 0.7 else lvl_votes if r <= 0.3 else []).append("reset")
            if r_n >= 2:
                (cum_votes if rc >= 0.7 else lvl_votes if rl >= 0.7 else []).append("ratio")
            if w_n < MIN_WITHIN:
                flag, basis = None, "insufficient_data"
            elif w < 0.6:
                flag, basis = 0, "not_monotonic"
            elif w >= 0.9 and cum_votes and not lvl_votes:
                flag, basis = 1, "monotonic+" + "+".join(cum_votes)
            elif lvl_votes and not cum_votes:
                flag, basis = 0, "level:" + "+".join(lvl_votes)
            else:
                flag, basis = None, "ambiguous"
        db.execute(
            "UPDATE indicators SET is_cumulative=?, cumulative_basis=?, within_score=?, reset_score=? WHERE id=?",
            (flag, basis, w, r, ind_id),
        )
        counts[(per, flag, basis)] += 1
        if per in ("Choraklik", "Oylik") and flag is None:
            review.append((ind_id, per, unit, name, basis, w, r))
    _apply_overrides(db, review)
    db.commit()

    out = settings.db_path.parent / "cumulative_review.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "periodicity", "unit", "name_uz", "basis", "within", "reset", "decision(0/1)"])
        wr.writerows(review)
    print(f"cumulative flags: {len(review)} need review (see {out.name})")
    for (per, flag, basis), n in sorted(counts.items(), key=lambda x: (str(x[0][0]), str(x[0][1]))):
        if per in ("Choraklik", "Oylik"):
            print(f"  {per:10} is_cumulative={flag!s:4} {basis:24} {n}")
    return counts
