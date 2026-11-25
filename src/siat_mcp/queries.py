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
