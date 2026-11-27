"""Stage 5: data access and exact calculations. No LLM involved, no number is ever invented.

Every successful result carries:
  source : which indicator/period the numbers came from (+ license)
  facts  : flat list of every number a final answer may quote (used by the stage-7 verifier)
Every failure has status != "ok" and a machine-readable reason (no_data / ambiguous / not_found / error).
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import forecast as fc
from .config import settings
from .normalize import REGION_ALIASES, normalize_text, parse_period_expr, region_key, token_match, tokens
from .search import Catalog

SITE = "https://siat.stat.uz/"
LICENSE = "CC BY 4.0 (Uzbekistan National Statistics Committee, siat.stat.uz)"
RATE_WORDS = ("foiz", "%", "indeks")


def _shift(label: str, *, years: int = 0, steps: int = 0) -> str | None:
    """Move a canonical period label by whole years and/or one-period steps."""
    m = re.fullmatch(r"(\d{4})(?:-(Q|M)(\d{1,2}))?", label)
    if not m:
        return None
    y, kind, n = int(m.group(1)) + years, m.group(2), int(m.group(3) or 0)
    if kind is None:
        return str(y + steps)
    per_year = 4 if kind == "Q" else 12
    idx = y * per_year + (n - 1) + steps
    y, n = divmod(idx, per_year)
    return f"{y}-{kind}{n + 1}" if kind == "Q" else f"{y}-M{n + 1:02d}"


def _num(x: float | None, nd: int = 4):
    return None if x is None else round(x, nd)


class Stats:
    def __init__(self, db_path: Path | str | None = None):
        path = Path(db_path or settings.db_path)
        self.db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.catalog = Catalog(self.db)
        self._members_norm: dict[int, tuple] = {}
        self._cache: dict[tuple, list] = {}

    # ------------------------------------------------------------------ catalog
    def search_indicators(self, query: str, limit: int = 8, periodicity: str | None = None) -> dict:
        hits = self.catalog.search(query, limit=limit, periodicity=periodicity)
        if not hits:
            return {"status": "no_match", "query": query,
                    "message": "No indicator in the SIAT catalog matches this query."}
        gap = hits[0].score - (hits[1].score if len(hits) > 1 else 0)
        return {"status": "ok", "query": query, "confident": hits[0].score >= 0.7 and gap >= 0.05,
                "results": [h.as_dict() for h in hits]}

    def _ind(self, ind_id) -> sqlite3.Row | None:
        try:
            return self.db.execute("SELECT * FROM indicators WHERE id=?", (int(ind_id),)).fetchone()
        except (TypeError, ValueError):
            return None

    def _source(self, ind: sqlite3.Row, period: str | list | None = None) -> dict:
        return {"indicator_id": ind["id"], "code": ind["code"], "name": ind["name_uz"],
                "unit": ind["unit_uz"], "periodicity": ind["periodicity"], "period": period,
                "provider": SITE, "license": LICENSE}

    def indicator_members(self, ind_id: int) -> list[sqlite3.Row]:
        key = ("members", ind_id)
        if key not in self._cache:
            self._cache[key] = self.db.execute(
                """SELECT m.* FROM members m WHERE m.id IN
                   (SELECT DISTINCT member_id FROM observations WHERE indicator_id=?) ORDER BY m.id""",
                (ind_id,),
            ).fetchall()
        return self._cache[key]

    def describe_indicator(self, indicator_id: int) -> dict:
        ind = self._ind(indicator_id)
        if not ind:
            return {"status": "not_found", "message": f"No indicator with id {indicator_id}."}
        members = self.indicator_members(ind["id"])
        meta = {}
        for item in json.loads(ind["metadata_json"] or "[]"):
            if item.get("name_en") in ("Calculation methodology", "Classifiers", "Note",
                                       "Primary source of information", "Name of department"):
                meta.setdefault(item["name_en"], []).append(item.get("value_uz") or item.get("value"))
        caveats = []
        if ind["periodicity"] in ("Choraklik", "Oylik"):
            if ind["is_cumulative"] == 1:
                caveats.append("Values are cumulative within the year (Q4/M12 = full year). "
                               "Use basis='period' for single-quarter/month values.")
            elif ind["is_cumulative"] is None:
                caveats.append("Unknown whether sub-annual values are cumulative: "
                               "period-over-period comparisons are refused for this indicator.")
        return {
            "status": "ok", "indicator_id": ind["id"], "code": ind["code"], "name": ind["name_uz"],
            "unit": ind["unit_uz"], "periodicity": ind["periodicity"],
            "period_range": [ind["first_period"], ind["last_period"]], "values": ind["n_values"],
            "is_cumulative": ind["is_cumulative"], "caveats": caveats,
            "members": [{"id": m["id"], "name": m["name_uz"], "is_region": bool(m["is_region"])}
                        for m in members[:60]],
            "members_total": len(members), "metadata": meta, "source": self._source(ind),
        }

    def list_regions(self) -> dict:
        rows = self.db.execute("SELECT id, name_uz FROM members WHERE is_region=1 ORDER BY id").fetchall()
        return {"status": "ok", "regions": [{"id": r["id"], "name": r["name_uz"]} for r in rows]}

    # ------------------------------------------------------------------ resolving
    def _norm_members(self, members) -> list[tuple]:
        out = []
        for m in members:
            if m["id"] not in self._members_norm:
                nm = normalize_text(m["name_uz"] or m["name_uzc"])
                self._members_norm[m["id"]] = (nm, region_key(m["name_uz"] or ""), tokens(m["name_uz"]))
            out.append((m, *self._members_norm[m["id"]]))
        return out

    def resolve_member(self, spec, ind_id: int | None = None, default_republic: bool = True) -> dict:
        members = self.indicator_members(ind_id) if ind_id else self.db.execute("SELECT * FROM members").fetchall()
        if spec is None or (isinstance(spec, str) and not spec.strip()):
            for m in members:
                if m["id"] == 1 and default_republic:
                    return {"status": "ok", "member": m, "defaulted": True}
            if len(members) == 1:
                return {"status": "ok", "member": members[0], "defaulted": True}
            return {"status": "ambiguous", "reason": "member_required",
                    "candidates": [{"id": m["id"], "name": m["name_uz"]} for m in members[:15]]}
        if isinstance(spec, int) or (isinstance(spec, str) and spec.strip().isdigit()):
            for m in members:
                if m["id"] == int(spec):
                    return {"status": "ok", "member": m}
        spec = str(spec)
        norm, rk = normalize_text(spec), region_key(spec)
        by_id = {m["id"]: m for m in members}
        alias = REGION_ALIASES.get(norm) or REGION_ALIASES.get(rk)
        if alias and alias in by_id:
            return {"status": "ok", "member": by_id[alias]}
        scored = []
        qt = norm.split()
        for m, nm, mk, mt in self._norm_members(members):
            if norm == nm:
                s = 1.0
            elif rk and rk == mk:
                s = 0.95
            elif qt and mt:
                cov = sum(max((token_match(q, t) for t in mt), default=0) for q in qt) / len(qt)
                prec = sum(max((token_match(q, t) for q in qt), default=0) for t in mt) / len(mt)
                s = 0.6 * cov + 0.4 * prec if cov >= 0.75 else 0.0
            else:
                s = 0.0
            if s > 0.55:
                scored.append((s, m))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            return {"status": "not_found", "query": spec,
                    "available": [{"id": m["id"], "name": m["name_uz"]} for m in members[:20]]}
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.08:
            return {"status": "ambiguous", "query": spec, "reason": "several members match",
                    "candidates": [{"id": m["id"], "name": m["name_uz"]} for _, m in scored[:6]]}
        return {"status": "ok", "member": scored[0][1]}

    def _series(self, ind_id: int, member_id: int) -> list[sqlite3.Row]:
        key = ("series", ind_id, member_id)
        if key not in self._cache:
            self._cache[key] = self.db.execute(
                """SELECT p.label, p.kind, p.year, p.quarter, p.month, p.sort_key, o.value
                   FROM observations o JOIN periods p ON p.id=o.period_id
                   WHERE o.indicator_id=? AND o.member_id=? ORDER BY p.sort_key""",
                (ind_id, member_id)).fetchall()
        return self._cache[key]

    def _resolve_period(self, ind: sqlite3.Row, series, expr) -> dict:
        have = {r["label"]: r for r in series}
        rng = {"first": series[0]["label"] if series else None, "last": series[-1]["label"] if series else None,
               "count": len(series)}
        p = parse_period_expr(str(expr))
        label = p["label"]
        if label is None:
            return {"status": "error", "reason": f"Cannot parse period '{expr}'. Use e.g. 2023, 2023-Q2, 2023-M05.",
                    "available_periods": rng}
        if label in have:
            return {"status": "ok", "label": label}
        # a whole year asked from a sub-annual series
        if re.fullmatch(r"\d{4}", label) and ind["periodicity"] in ("Choraklik", "Oylik"):
            y = int(label)
            if f"{label}-Q5" in have:
                return {"status": "ok", "label": f"{label}-Q5", "note": "full-year value stored as YYYY-Q5"}
            end = f"{label}-Q4" if ind["periodicity"] == "Choraklik" else f"{label}-M12"
            if ind["is_cumulative"] == 1 and end in have:
                return {"status": "ok", "label": end,
                        "note": f"series is cumulative within the year, so {end} is the full-year {y} value"}
            return {"status": "no_data", "reason": f"Indicator is {ind['periodicity'].lower()}; no full-year {y} value. "
                    f"Ask for a quarter/month.", "available_periods": rng}
        # a quarter/month asked from an annual series
        if ind["periodicity"] == "Yillik" and p["year"] and (p["quarter"] or p["month"]):
            return {"status": "no_data", "reason": "Indicator is annual; no quarterly/monthly values.",
                    "available_periods": rng}
        return {"status": "no_data", "reason": f"Period {label} is not in the data.", "available_periods": rng}

    def _value_at(self, ind, series, label: str, basis: str):
        """-> (value, note, error_reason). basis 'period' de-cumulates cumulative sub-annual series."""
        have = {r["label"]: r["value"] for r in series}
        v = have.get(label)
        if v is None:
            return None, None, "source has no value for this period"
        if basis == "period" and ind["periodicity"] in ("Choraklik", "Oylik"):
            if ind["is_cumulative"] is None:
                return None, None, "unknown whether series is cumulative (needs review)"
            if ind["is_cumulative"] == 1:
                m = re.fullmatch(r"(\d{4})-(Q|M)(\d{1,2})", label)
                if m and int(m.group(3)) > 1:
                    prev = have.get(_shift(label, steps=-1))
                    if prev is None:
                        return None, None, "previous period missing; cannot de-cumulate"
                    return v - prev, "single-period value = cumulative(this) - cumulative(previous)", None
                return v, "first period of the year: cumulative value equals the period value", None
        return v, None, None

    def _prep(self, indicator_id, region):
        ind = self._ind(indicator_id)
        if not ind:
            return None, None, {"status": "not_found", "message": f"No indicator with id {indicator_id}."}
        mem = self.resolve_member(region, ind["id"])
        if mem["status"] != "ok":
            return None, None, {**mem, "indicator_id": ind["id"]}
        return ind, mem, None

    # ------------------------------------------------------------------ tools
    def get_value(self, indicator_id: int, period: str, region=None, basis: str = "reported") -> dict:
        ind, mem, err = self._prep(indicator_id, region)
        if err:
            return err
        m = mem["member"]
        series = self._series(ind["id"], m["id"])
        pr = self._resolve_period(ind, series, period)
        if pr["status"] != "ok":
            return {**pr, "indicator_id": ind["id"], "member": m["name_uz"], "source": self._source(ind)}
        v, note, why = self._value_at(ind, series, pr["label"], basis)
        if why:
            return {"status": "no_data", "reason": why, "indicator_id": ind["id"], "member": m["name_uz"],
                    "period": pr["label"], "source": self._source(ind, pr["label"])}
        notes = [n for n in (pr.get("note"), note, "member defaulted to the Republic" if mem.get("defaulted") else None) if n]
        return {"status": "ok", "indicator": ind["name_uz"], "member": {"id": m["id"], "name": m["name_uz"]},
                "period": pr["label"], "value": v, "unit": ind["unit_uz"], "basis": basis, "notes": notes,
                "facts": [{"kind": "value", "value": v, "period": pr["label"], "member": m["name_uz"]}],
                "source": self._source(ind, pr["label"])}

    def get_series(self, indicator_id: int, region=None, start: str | None = None, end: str | None = None,
                   basis: str = "reported") -> dict:
        ind, mem, err = self._prep(indicator_id, region)
        if err:
            return err
        m = mem["member"]
        series = self._series(ind["id"], m["id"])
        lo = parse_period_expr(start)["label"] if start else None
        hi = parse_period_expr(end)["label"] if end else None
        pts, notes = [], []
        for r in series:
            if (lo and r["sort_key"] < _sort_key(lo)) or (hi and r["sort_key"] > _sort_key(hi)):
                continue
            v, note, why = self._value_at(ind, series, r["label"], basis)
            if why and basis == "period":
                notes.append(f"{r['label']}: {why}")
            if v is not None:
                pts.append({"period": r["label"], "value": v})
        if not pts:
            return {"status": "no_data", "reason": "no values in the requested range", "indicator_id": ind["id"],
                    "member": m["name_uz"], "available_periods": {"first": ind["first_period"], "last": ind["last_period"]}}
        return {"status": "ok", "indicator": ind["name_uz"], "member": {"id": m["id"], "name": m["name_uz"]},
                "unit": ind["unit_uz"], "basis": basis, "points": pts, "notes": notes[:5],
                "facts": [{"kind": "value", "value": p["value"], "period": p["period"], "member": m["name_uz"]} for p in pts],
                "source": self._source(ind, [pts[0]["period"], pts[-1]["period"]])}

    # ------------------------------------------------------------------ forecasting
    def forecast(self, indicator_id: int, period: str | None = None, region=None) -> dict:
        """Estimate a period AFTER the last published value. Never used for periods that exist or past gaps."""
        ind, mem, err = self._prep(indicator_id, region)
        if err:
            return err
        m = mem["member"]
        base = {"indicator_id": ind["id"], "indicator": ind["name_uz"], "member": {"id": m["id"], "name": m["name_uz"]},
                "unit": ind["unit_uz"], "source": self._source(ind)}

        def refuse(reason: str, **extra) -> dict:
            return {**base, "status": "refused", "reason": reason, **extra}

        kind = {"Yillik": "year", "Choraklik": "quarter", "Oylik": "month"}.get(ind["periodicity"])
        if not kind:
            return refuse("not_a_time_series")
        season = fc.SEASON[kind]
        rows = [r for r in self._series(ind["id"], m["id"]) if r["kind"] == kind and r["value"] is not None]
        # only the unbroken tail of the series can be modelled
        start = len(rows) - 1
        while start > 0 and _shift(rows[start - 1]["label"], steps=1) == rows[start]["label"]:
            start -= 1
        rows = rows[max(start, 0):]
        if not rows:
            return refuse("too_short", points=0)
        last = rows[-1]["label"]

        # target label
        if period is None:
            target = _shift(last, steps=1)
        else:
            target = parse_period_expr(str(period))["label"]
            if target is None:
                return refuse("bad_period")
            if re.fullmatch(r"\d{4}", target) and kind != "year":  # whole year of a sub-annual series
                if ind["is_cumulative"] != 1:
                    return refuse("no_full_year")
                target = f"{target}-Q4" if kind == "quarter" else f"{target}-M12"
            shape = {"year": r"\d{4}", "quarter": r"\d{4}-Q[1-4]", "month": r"\d{4}-M\d{2}"}[kind]
            if not re.fullmatch(shape, target):
                return refuse("wrong_frequency", periodicity=ind["periodicity"])
        if _sort_key(target) <= rows[-1]["sort_key"]:
            return refuse("not_future", last_period=last)

        horizon, label = 0, last
        labels = []
        while label != target and horizon <= fc.MAX_HORIZON[kind]:
            label = _shift(label, steps=1)
            labels.append(label)
            horizon += 1
        if horizon > fc.MAX_HORIZON[kind] or label != target:
            return refuse("too_far", last_period=last, max_period=_shift(last, steps=fc.MAX_HORIZON[kind]))
        if len(rows) < fc.MIN_POINTS[kind]:
            return refuse("too_short", points=len(rows))

        # cumulative sub-annual series: model single-period values, then accumulate again
        cumulative = kind != "year" and ind["is_cumulative"] == 1
        if kind != "year" and ind["is_cumulative"] is None:
            return refuse("unknown_cumulative")
        if cumulative:
            pos = "month" if kind == "month" else "quarter"  # month rows carry a quarter too
            first = next((i for i, r in enumerate(rows) if r[pos] == 1), None)
            if first is None:
                return refuse("too_short", points=0)
            rows = rows[first:]
            values = [r["value"] - (rows[i - 1]["value"] if r[pos] > 1 else 0) for i, r in enumerate(rows)]
            if len(values) < fc.MIN_POINTS[kind]:
                return refuse("too_short", points=len(values))
        else:
            values = [r["value"] for r in rows]

        res = fc.forecast(values, horizon, season)
        if not res["ok"]:
            return refuse(res["reason"], error_pct=None if res.get("error") is None else round(res["error"] * 100, 1),
                          points=len(values))

        digits = 1 if abs(values[-1]) >= 100 else 2
        path, run = [], rows[-1]["value"] if cumulative else 0.0
        run_low, run_high = run, run
        for lab, (p, lo, hi) in zip(labels, res["points"]):
            if cumulative:
                if lab.endswith(("-Q1", "-M01")):
                    run = run_low = run_high = 0.0
                run, run_low, run_high = run + p, run_low + lo, run_high + hi
                p, lo, hi = run, run_low, run_high
            path.append({"period": lab, "value": round(p, digits), "low": round(lo, digits), "high": round(hi, digits)})
        point, prev = path[-1], rows[-1]
        error_pct = round(res["error"] * 100, 1)
        # compare with the same period one year earlier when cumulative, else with the last published value
        ref_label = _shift(target, years=-1) if cumulative else last
        ref = next((r["value"] for r in rows if r["label"] == ref_label), None)
        if ref is None and cumulative:
            ref = next((x["value"] for x in path if x["period"] == ref_label), None)
        change_pct = round((point["value"] - ref) / abs(ref) * 100, 1) if ref else None
        notes = ["Bu rasmiy ma'lumot emas, o'tgan davrlar asosida hisoblangan taxmin."]
        if cumulative:
            notes.append("Qiymat yil boshidan jamlanma ko'rinishida berilgan.")
        if "so'm" in (ind["unit_uz"] or "").replace("‘", "'").replace("’", "'").lower():
            notes.append("Pul ko'rsatkichi joriy narxlarda: prognoz narx o'sishini ham o'z ichiga oladi.")

        facts = [{"kind": "forecast", "name": "prognoz", "value": point["value"], "period": target, "member": m["name_uz"]},
                 {"kind": "forecast", "name": "quyi chegara", "value": point["low"]},
                 {"kind": "forecast", "name": "yuqori chegara", "value": point["high"]},
                 {"kind": "calc", "name": "backtest_xatosi_foiz", "value": error_pct},
                 {"kind": "value", "value": prev["value"], "period": last, "member": m["name_uz"]}]
        if change_pct is not None:
            facts.append({"kind": "calc", "name": "ozgarish_foiz", "value": change_pct})
        return {**base, "status": "ok", "forecast": True, "period": target, "value": point["value"],
                "low": point["low"], "high": point["high"], "model": res["model"],
                "model_name": res["model_uz"], "error_pct": error_pct, "backtest_points": res["backtest_points"],
                "horizon": horizon, "last_actual": {"period": last, "value": prev["value"]},
                "change_pct": change_pct, "change_vs": ref_label if change_pct is not None else None,
                "history": [{"period": r["label"], "value": r["value"]} for r in rows][-max(12, 3 * season):],
                "path": path, "cumulative": cumulative, "notes": notes, "facts": facts,
                "source": self._source(ind, [rows[0]["label"], last])}

    def calc_change(self, indicator_id: int, period_from: str, period_to: str, region=None,
                    basis: str = "reported") -> dict:
        """Absolute and percentage change between two explicit periods."""
        a = self.get_value(indicator_id, period_from, region, basis)
        if a["status"] != "ok":
            return {**a, "failed_at": "period_from"}
        b = self.get_value(indicator_id, period_to, region, basis)
        if b["status"] != "ok":
            return {**b, "failed_at": "period_to"}
        return self._change_result(a, b)

    def _change_result(self, a: dict, b: dict) -> dict:
        va, vb = a["value"], b["value"]
        diff = round(vb - va, 6)
        pct = None if va == 0 else diff / abs(va) * 100
        notes = list(dict.fromkeys(a["notes"] + b["notes"]))
        if va == 0:
            notes.append("starting value is 0: percentage change is undefined")
        if any(w in (a["unit"] or "").lower() for w in RATE_WORDS):
            notes.append("unit is a percent/index: the absolute difference is in percentage points")
        if "joriy narx" in normalize_text(a["indicator"]):
            notes.append("values are at current prices (nominal): this is not real/inflation-adjusted growth")
        mem = a["member"]["name"]
        return {"status": "ok", "indicator": a["indicator"], "member": a["member"], "unit": a["unit"],
                "from": {"period": a["period"], "value": va}, "to": {"period": b["period"], "value": vb},
                "absolute_change": diff, "percent_change": _num(pct, 2),
                "direction": "increase" if diff > 0 else "decrease" if diff < 0 else "no change",
                "formula": "percent_change = (to - from) / |from| * 100", "notes": notes,
                "facts": [{"kind": "value", "value": va, "period": a["period"], "member": mem},
                          {"kind": "value", "value": vb, "period": b["period"], "member": mem},
                          {"kind": "calc", "value": diff, "name": "absolute_change"}]
                         + ([{"kind": "calc", "value": _num(pct, 2), "name": "percent_change"}] if pct is not None else []),
                "source": {**a["source"], "period": [a["period"], b["period"]]}}

    def calc_growth(self, indicator_id: int, period: str, region=None, versus: str = "previous_year") -> dict:
        """Growth of `period` vs the same period of the previous year, or vs the previous period."""
        ind = self._ind(indicator_id)
        if not ind:
            return {"status": "not_found", "message": f"No indicator with id {indicator_id}."}
        if versus not in ("previous_year", "previous_period"):
            return {"status": "error", "reason": "versus must be 'previous_year' or 'previous_period'"}
        cur = self.get_value(indicator_id, period, region)
        if cur["status"] != "ok":
            return cur
        label = cur["period"]
        basis = "reported"
        if versus == "previous_year":
            prev_label = _shift(label, years=-1)
        else:
            prev_label = _shift(label, steps=-1)
            if ind["periodicity"] in ("Choraklik", "Oylik"):
                if ind["is_cumulative"] is None:
                    return {"status": "refused", "reason": "Unknown whether this sub-annual series is cumulative; "
                            "period-over-period change would be unreliable.", "indicator_id": ind["id"]}
                basis = "period"  # de-cumulate so Q2 is compared with Q1 as single quarters
                cur = self.get_value(indicator_id, label, region, "period")
                if cur["status"] != "ok":
                    return cur
        if prev_label is None:
            return {"status": "error", "reason": f"Cannot derive a comparison period from {label}"}
        prev = self.get_value(indicator_id, prev_label, region, basis)
        if prev["status"] != "ok":
            return {**prev, "failed_at": "comparison_period", "comparison_period": prev_label}
        res = self._change_result(prev, cur)
        res["versus"] = versus
        if basis == "period":
            res["notes"].append("compared as single-period values (cumulative series de-cumulated)")
        return res

    def compare_regions(self, indicator_id: int, regions: list, period: str, basis: str = "reported") -> dict:
        ind = self._ind(indicator_id)
        if not ind:
            return {"status": "not_found", "message": f"No indicator with id {indicator_id}."}
        if not regions or len(regions) < 2:
            return {"status": "error", "reason": "give at least two regions/members to compare"}
        rows, problems = [], []
        for r in regions:
            v = self.get_value(indicator_id, period, r, basis)
            if v["status"] == "ok":
                rows.append(v)
            else:
                problems.append({"region": r, **{k: v.get(k) for k in ("status", "reason", "candidates")}})
        if problems:
            return {"status": "incomplete", "indicator_id": ind["id"], "problems": problems,
                    "message": "Not every requested region could be resolved or has data; no comparison made."}
        rows.sort(key=lambda x: -x["value"])
        top, low = rows[0], rows[-1]
        facts = [f for r in rows for f in r["facts"]]
        out = {"status": "ok", "indicator": ind["name_uz"], "unit": ind["unit_uz"], "period": rows[0]["period"],
               "ranking": [{"rank": i + 1, "member": r["member"]["name"], "value": r["value"]}
                           for i, r in enumerate(rows)],
               "highest": {"member": top["member"]["name"], "value": top["value"]},
               "lowest": {"member": low["member"]["name"], "value": low["value"]},
               "difference_highest_lowest": round(top["value"] - low["value"], 6),
               "ratio_highest_to_lowest": None if low["value"] == 0 else _num(top["value"] / low["value"], 3),
               "source": self._source(ind, rows[0]["period"])}
        facts.append({"kind": "calc", "value": out["difference_highest_lowest"], "name": "difference_highest_lowest"})
        if out["ratio_highest_to_lowest"] is not None:
            facts.append({"kind": "calc", "value": out["ratio_highest_to_lowest"], "name": "ratio_highest_to_lowest"})
        out["facts"] = facts
        return out

    def top_bottom(self, indicator_id: int, period: str, order: str = "highest", limit: int = 5,
                   regions_only: bool = True, basis: str = "reported") -> dict:
        ind = self._ind(indicator_id)
        if not ind:
            return {"status": "not_found", "message": f"No indicator with id {indicator_id}."}
        if order not in ("highest", "lowest"):
            return {"status": "error", "reason": "order must be 'highest' or 'lowest'"}
        members = [m for m in self.indicator_members(ind["id"])
                   if (m["is_region"] and m["id"] != 1) or (not regions_only and m["id"] != 1)]
        if not members:
            return {"status": "no_data", "reason": "this indicator has no regional/member breakdown"}
        rows, label, skipped = [], None, 0
        for m in members:
            series = self._series(ind["id"], m["id"])
            pr = self._resolve_period(ind, series, period)
            if pr["status"] != "ok":
                skipped += 1
                continue
            v, _, why = self._value_at(ind, series, pr["label"], basis)
            if v is None:
                skipped += 1
                continue
            label = pr["label"]
            rows.append((m["name_uz"], v))
        if not rows:
            return {"status": "no_data", "reason": f"no member has a value for '{period}'",
                    "available_periods": {"first": ind["first_period"], "last": ind["last_period"]}}
        rows.sort(key=lambda x: -x[1] if order == "highest" else x[1])
        top = rows[:max(1, min(limit, 30))]
        return {"status": "ok", "indicator": ind["name_uz"], "unit": ind["unit_uz"], "period": label,
                "order": order, "ranking": [{"rank": i + 1, "member": n, "value": v} for i, (n, v) in enumerate(top)],
                "members_compared": len(rows), "members_without_value": skipped,
                "facts": [{"kind": "value", "value": v, "period": label, "member": n} for n, v in top],
                "source": self._source(ind, label)}


def _sort_key(label: str) -> int:
    m = re.fullmatch(r"(\d{4})(?:-(Q|M)(\d{1,2}))?", label)
    if not m:
        return 0
    y = int(m.group(1))
    if not m.group(2):
        return y * 100 + 13
    n = int(m.group(3))
    return y * 100 + (n * 3 if m.group(2) == "Q" else n)
