"""Out-of-sample accuracy of the forecaster.

For a random sample of series the last published points are HIDDEN, the forecaster (model selection
included) sees only the earlier data, and its estimate is compared with the real value. The naive guess
("same as the last known value", or "same period last year" for sub-annual data) is the baseline.

Run: python eval/forecast_backtest.py [seed]
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from siat_mcp import forecast as fc
from siat_mcp.queries import Stats, _shift

PER_KIND = 600          # series sampled per periodicity
HOLDOUT = {"year": 2, "quarter": 4, "month": 6}
KINDS = {"year": "Yillik", "quarter": "Choraklik", "month": "Oylik"}


def series_values(stats, ind_id, member_id, kind, cumulative):
    rows = [r for r in stats._series(ind_id, member_id) if r["kind"] == kind and r["value"] is not None]
    start = len(rows) - 1
    while start > 0 and _shift(rows[start - 1]["label"], steps=1) == rows[start]["label"]:
        start -= 1
    rows = rows[max(start, 0):]
    if not cumulative:
        return [r["value"] for r in rows]
    pos = "month" if kind == "month" else "quarter"
    first = next((i for i, r in enumerate(rows) if r[pos] == 1), None)
    if first is None:
        return []
    rows = rows[first:]
    return [r["value"] - (rows[i - 1]["value"] if r[pos] > 1 else 0) for i, r in enumerate(rows)]


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] + xs[(n - 1) // 2]) / 2


def main() -> None:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    random.seed(seed)
    stats = Stats()
    report = {}
    for kind, periodicity in KINDS.items():
        season, hold = fc.SEASON[kind], HOLDOUT[kind]
        extra = "" if kind == "year" else " AND is_cumulative IS NOT NULL"
        ids = [r["id"] for r in stats.db.execute(
            f"SELECT id FROM indicators WHERE periodicity=? AND n_values>0{extra} ORDER BY id", (periodicity,))]
        random.shuffle(ids)
        err, base, inside, tried, refused = [], [], [], 0, 0
        for ind_id in ids:
            if tried >= PER_KIND:
                break
            members = stats.indicator_members(ind_id)
            m = random.choice(members)
            cum = kind != "year" and stats._ind(ind_id)["is_cumulative"] == 1
            values = series_values(stats, ind_id, m["id"], kind, cum)
            if len(values) < fc.MIN_POINTS[kind] + hold:
                continue
            train, test = values[:-hold], values[-hold:]
            if any(v == 0 for v in test) or len(set(train)) < 3:
                continue
            tried += 1
            res = fc.forecast(train, hold, season)
            if not res["ok"]:
                refused += 1
                continue
            naive = [train[-season + (i % season)] for i in range(hold)] if season > 1 else [train[-1]] * hold
            for (p, lo, hi), b, actual in zip(res["points"], naive, test):
                err.append(abs(p - actual) / abs(actual))
                base.append(abs(b - actual) / abs(actual))
                inside.append(lo <= actual <= hi)
        report[kind] = {
            "series_tried": tried, "refused": refused, "forecast_points": len(err),
            "median_error_pct": round(100 * median(err), 1),
            "median_error_naive_pct": round(100 * median(base), 1),
            "within_10pct": round(100 * sum(e <= 0.10 for e in err) / len(err), 1),
            "interval_coverage_pct": round(100 * sum(inside) / len(inside), 1),
        }
        print(kind, report[kind], flush=True)
    out = Path(__file__).with_name("forecast_backtest.json" if seed == 7 else f"forecast_backtest_seed{seed}.json")
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
