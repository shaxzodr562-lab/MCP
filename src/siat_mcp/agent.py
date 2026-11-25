"""Stages 6-7: question understanding, orchestration, answer templates and the number verifier.

This is the deterministic ("rules") engine. It needs no LLM, so it is also the fallback when an
external model is unavailable. Pipeline per question:

  split -> detect regions / periods / intent -> search indicator -> call exact tools
        -> build Uzbek answer from templates -> verify every number against tool facts
"""
from __future__ import annotations

import re
import time
from typing import Any

from .normalize import MONTHS, ORDINALS, ROMAN, normalize_text, parse_period_expr, region_key, token_match
from .queries import Stats, _sort_key

MAX_PARTS = 3
SOURCE_NAME = "siat.stat.uz"

# --------------------------------------------------------------------------- vocabulary
GROWTH_WORDS = {"osdi", "osish", "osgan", "ortdi", "oshdi", "oshgan", "kamaydi", "kamaygan", "pasaydi",
                "pasaygan", "kopaydi", "ozgardi", "ozgargan", "ortgan", "tushdi", "tushgan", "kotarildi"}
GROWTH_PHRASES = ("necha foiz", "qancha foiz", "necha foizga", "qanchaga", "nechaga", "foizga")
SERIES_WORDS = {"dinamika", "dinamikasi", "dinamikada", "tarixi", "grafik", "trend", "ozgarishi", "ozgarish"}
SERIES_PHRASES = ("yillar boyicha", "yillar davomida", "oxirgi yillarda", "yil davomida")
COMPARE_WORDS = {"solishtir", "solishtiring", "solishtirib", "taqqosla", "taqqoslang", "taqqoslab", "farqi", "farq",
                 "qiyosla", "qiyoslang"}
TOP_HIGH = {"yuqori", "kop", "katta", "eng", "birinchi"}
TOP_LOW = {"past", "kam", "kichik"}
LATEST_WORDS = {"oxirgi", "songgi", "hozirgi", "joriy", "bugungi", "yangi"}
FORECAST_WORDS = {"prognoz", "bashorat", "kutilmoqda", "kelgusida", "kelajakda", "boladi", "bolishi", "rejalashtirilgan"}
CITY_WORDS = {"shahri", "shahar", "shahrida", "shaharda", "poytaxt", "poytaxti"}
REGION_WORDS = {"viloyati", "viloyat", "viloyatida", "viloyatning"}
REPUBLIC_WORDS = {"respublika", "respublikada", "respublikasi", "ozbekiston", "ozbekistonda", "mamlakat",
                  "mamlakatda", "ozbekistonning", "mamlakatimiz"}
NOISE = {"qandaydir", "aloqador", "bogliq", "chiqar", "chiqarchi", "chiqaring", "chiqarib", "bering", "ber", "statistika",
         "statistikasi", "statistikani", "korsatchi", "umumiy", "asosiy", "korsatkich", "korsatkichlar", "korsatkichi", "narsa",
         "biror", "nimadir", "qiziqarli", "tahlil", "necha", "qancha", "nechta", "qanchaga", "nechaga", "foiz", "foizga", "edi", "boldi", "bor", "ayt", "ayting",
         "korsat", "korsating", "menga", "iltimos", "qaysi", "qanday", "nima", "haqida", "malumot", "yil", "yilda",
         "yilgi", "yilning", "yillarda", "chorak", "choragida", "oyida", "oy", "dan", "gacha", "nisbatan", "bilan",
         "va", "ham", "uchun", "boyicha", "bu", "eng", "ta", "da", "ning", "ni", "ga", "bolgan", "bolsa", "yillar",
         "oxirgi", "songgi", "hozirgi", "joriy", "bugungi", "yangi", "mamlakat", "mamlakatda", "respublika",
         "respublikada", "ozbekiston", "ozbekistonda", "viloyat", "viloyati", "shahri", "shahar"}


# --------------------------------------------------------------------------- formatting
def fmt(v: float | None) -> str:
    """Uzbek number format: '1 433 975,7' (thousands = space, decimal = comma); keeps source precision."""
    if v is None:
        return "—"
    s = repr(round(float(v), 6))
    if "e" in s or "E" in s:
        s = f"{float(v):.6f}"
    ip, _, dp = s.partition(".")
    dp = dp.rstrip("0")[:4]
    neg = ip.startswith("-")
    ip = ip.lstrip("-")
    ip = f"{int(ip):,}".replace(",", " ")
    return ("-" if neg else "") + ip + ("," + dp if dp else "")


def period_human(label: str | None) -> str:
    if not label:
        return "—"
    m = re.fullmatch(r"(\d{4})(?:-(Q|M)(\d{1,2}))?", label)
    if not m:
        return label
    y = m.group(1)
    if not m.group(2):
        return f"{y}-yil"
    n = int(m.group(3))
    if m.group(2) == "Q":
        return f"{y}-yil {n}-chorak" if n <= 4 else f"{y}-yil (yillik jami)"
    names = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]
    return f"{y}-yil {names[n - 1]}"


def member_human(name: str | None) -> str:
    return name or "—"


# --------------------------------------------------------------------------- verifier (stage 7)
_NUM_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?: \d{3})+|\d+)(?:,(\d+))?")


def verify_answer(text: str, facts: list[dict], ignore: list[str] | None = None) -> dict:
    """Every number in `text` must equal (to the printed precision) a number from the tool facts.

    Years, "N-chorak", indicator names/ids and other `ignore` strings are removed before scanning.
    """
    scan = text
    for s in sorted((ignore or []), key=len, reverse=True):
        if s:
            scan = scan.replace(s, " ")
    scan = re.sub(r"\b(?:19|20)\d{2}\b(?:-yil\w*)?", " ", scan)
    scan = re.sub(r"\b\d{1,2}-(?:chorak|oy)\w*", " ", scan)
    scan = re.sub(r"#\d+", " ", scan)
    allowed = [abs(float(f["value"])) for f in facts if isinstance(f.get("value"), (int, float))]
    checked, unmatched = [], []
    for m in _NUM_RE.finditer(scan):
        ip, dp = m.group(1).replace(" ", ""), m.group(2)
        val = float(ip + ("." + dp if dp else ""))
        tol = 0.5 * 10 ** -(len(dp) if dp else 0) + 1e-9
        ok = any(abs(val - a) <= tol for a in allowed)
        checked.append(m.group(0))
        if not ok:
            unmatched.append(m.group(0))
    return {"checked": len(checked), "matched": len(checked) - len(unmatched), "unmatched": unmatched,
            "passed": not unmatched}


# --------------------------------------------------------------------------- parsing
def split_questions(text: str) -> list[str]:
    parts = re.split(r"\?+|;|\n|\bhamda\b|\bshuningdek\b|\bva yana\b", text, flags=re.I)
    parts = [p.strip(" ,.!") for p in parts if len(p.strip(" ,.!")) >= 3]
    return parts[:MAX_PARTS] or [text.strip()]


def find_periods(norm_tokens: list[str]) -> list[str]:
    """All periods mentioned: years, each optionally refined by a nearby quarter/month word."""
    years = [(i, t) for i, t in enumerate(norm_tokens) if re.fullmatch(r"(?:19|20)\d{2}", t)]
    mentions: list[tuple[int, str, int]] = []  # (position, 'Q'|'M', number)
    for i, t in enumerate(norm_tokens):
        nxt = norm_tokens[i + 1] if i + 1 < len(norm_tokens) else ""
        if nxt.startswith(("chorak", "kvartal")):
            n = int(t) if t.isdigit() and 1 <= int(t) <= 4 else ORDINALS.get(t) or ROMAN.get(t)
            if n:
                mentions.append((i, "Q", n))
        mq = re.fullmatch(r"q([1-4])", t)
        if mq:
            mentions.append((i, "Q", int(mq.group(1))))
        for name, num in MONTHS.items():
            if t.startswith(name) and len(t) - len(name) <= 4:
                mentions.append((i, "M", num))
                break
    labels = {i: y for i, y in years}
    assigned: dict[int, tuple[str, int]] = {}
    for pos, kind, num in mentions:
        if not years:
            continue
        yi = min(years, key=lambda x: (abs(x[0] - pos), x[0]))[0]
        if abs(yi - pos) <= 5 and yi not in assigned:
            assigned[yi] = (kind, num)
    out = []
    for i, y in years:
        if i in assigned:
            kind, num = assigned[i]
            out.append(f"{y}-Q{num}" if kind == "Q" else f"{y}-M{num:02d}")
        else:
            out.append(y)
    return list(dict.fromkeys(out))


def partial_period(norm_tokens: list[str]) -> str | None:
    """Quarter/month without a year, e.g. '2-chorak' -> 'Q2'/'M03' marker (year resolved later)."""
    for i, t in enumerate(norm_tokens):
        nxt = norm_tokens[i + 1] if i + 1 < len(norm_tokens) else ""
        if nxt.startswith(("chorak", "kvartal")) and t.isdigit() and 1 <= int(t) <= 4:
            return f"Q{int(t)}"
        for name, num in MONTHS.items():
            if t.startswith(name) and len(t) - len(name) <= 4:
                return f"M{num:02d}"
    return None


# --------------------------------------------------------------------------- engine
class Agent:
    def __init__(self, stats: Stats):
        self.s = stats
        self.regions = self.s.db.execute("SELECT id, name_uz FROM members WHERE is_region=1 ORDER BY id").fetchall()
        self._rkeys = {r["id"]: normalize_text(region_key(r["name_uz"])).split() for r in self.regions}

    # ---- public
    def ask(self, question: str, *, indicator_id: int | None = None, region: str | None = None,
            period: str | None = None) -> dict:
        t0 = time.time()
        question = (question or "").strip()[:600]
        if not question:
            return {"question": question, "parts": [], "elapsed_ms": 0}
        original = split_questions(question)
        rw = None
        if indicator_id is None:  # a clicked chip already carries exact parameters: no need to interpret
            from . import llm
            rw = llm.rewrite(question, [r["name_uz"] for r in self.regions if r["id"] != 1])
        if rw and not rw.get("on_topic", True):  # Gemini says it is not a statistics question: refuse via the original text
            rw = None
        if not rw:
            parts = [self._answer_part(q, indicator_id, region, period) for q in original]
            return {"question": question, "parts": parts, "engine": "rules",
                    "elapsed_ms": round((time.time() - t0) * 1000)}
        parts = []
        for i, q in enumerate(rw["questions"]):
            p = self._answer_part(q, None, region, period)
            if p["status"] in ("no_match", "not_found") and len(rw["questions"]) == len(original):
                alt = self._answer_part(original[i], None, region, period)  # LLM rewrite didn't help: try as typed
                if alt["status"] == "ok":
                    p = alt
            p["interpreted_as"] = q
            parts.append(p)
        return {"question": question, "parts": parts, "engine": "gemini+rules", "language": rw["language"],
                "elapsed_ms": round((time.time() - t0) * 1000)}

    # ---- region / period / intent detection
    def _detect_regions(self, toks: list[str]):
        found, ambiguous, consumed = [], None, set()
        for rid, keys in self._rkeys.items():
            if rid == 1 or not keys:
                continue
            idx = [i for i, t in enumerate(toks) if any(token_match(t, k) >= 0.75 for k in keys)]
            if not idx:
                continue
            consumed.update(toks[i] for i in idx)
            if keys == ["toshkent"]:
                continue  # handled below (city vs province)
            found.append((min(idx), rid))
        tk = [i for i, t in enumerate(toks) if token_match(t, "toshkent") >= 0.75]
        if tk:
            consumed.update(toks[i] for i in tk)
            has_city = any(t.startswith(("shahr", "shahar", "poytaxt")) for t in toks)
            has_reg = any(t.startswith("viloyat") for t in toks)
            if has_city and has_reg:
                found += [(tk[0], 3), (tk[-1] + 0.5, 4)]
            elif has_city:
                found.append((tk[0], 3))
            elif has_reg:
                found.append((tk[0], 4))
            else:
                ambiguous = "Toshkent"
        found.sort()
        ids = [rid for _, rid in found]
        if not ids and any(t in REPUBLIC_WORDS for t in toks):
            ids = [1]
        consumed.update(t for t in toks if t in CITY_WORDS | REGION_WORDS | REPUBLIC_WORDS)
        return ids, ambiguous, consumed

    def _intent(self, toks: list[str], text: str, n_regions: int, n_periods: int) -> str:
        ts = set(toks)
        if "eng" in ts and (ts & (TOP_HIGH | TOP_LOW)) - {"eng", "birinchi"}:
            return "top"
        if ts & COMPARE_WORDS or n_regions >= 2:
            return "compare" if n_regions >= 2 or n_periods < 2 else "growth"
        if ts & SERIES_WORDS or any(p in text for p in SERIES_PHRASES):
            return "series"
        if ts & GROWTH_WORDS or any(p in text for p in GROWTH_PHRASES):
            return "growth"
        if n_periods >= 2 and ("dan" in ts or "gacha" in ts):
            return "series"
        if n_periods >= 2:
            return "growth"
        return "value"

    # ---- main per-question logic
    def _answer_part(self, q: str, forced_ind, forced_region, forced_period) -> dict:
        norm = normalize_text(q)
        toks = norm.split()
        trace: list[dict] = []
        m = re.search(r"#(\d{1,5})", q)
        if m and not forced_ind:
            forced_ind = int(m.group(1))

        region_ids, ambiguous, consumed = self._detect_regions(toks)
        if forced_region:
            r = self.s.resolve_member(forced_region)
            if r["status"] == "ok":
                region_ids, ambiguous = [r["member"]["id"]], None
        if ambiguous:
            return self._part("ambiguous", q, answer="«Toshkent» deganda qaysi birini nazarda tutyapsiz?",
                              clarify=[{"label": "Toshkent shahri", "region": "Toshkent shahri"},
                                       {"label": "Toshkent viloyati", "region": "Toshkent viloyati"}], trace=trace)

        periods = [forced_period] if forced_period else find_periods(toks)
        partial = None if periods else partial_period(toks)
        latest = bool(set(toks) & LATEST_WORDS) or not periods
        intent = self._intent(toks, norm, len(region_ids), len(periods))
        forecast = bool(set(toks) & FORECAST_WORDS)

        # ---- indicator
        hits, rate_hit, q_tokens = [], None, []
        if forced_ind:
            d = self.s.describe_indicator(forced_ind)
            if d["status"] != "ok":
                return self._part("not_found", q, answer=f"#{forced_ind} raqamli ko'rsatkich topilmadi.", trace=trace)
            hits = [type("H", (), {"indicator_id": forced_ind, "score": 1.0, "as_dict": lambda self, i=forced_ind: {"indicator_id": i}})()]
        else:
            skip = consumed | NOISE | GROWTH_WORDS | SERIES_WORDS | COMPARE_WORDS | TOP_HIGH | TOP_LOW | FORECAST_WORDS
            query = " ".join(t for t in toks if t not in skip and not re.fullmatch(r"\d+", t) and not
                             any(t.startswith(n) and len(t) - len(n) <= 4 for n in MONTHS))
            if not query:
                if region_ids:
                    return self._profile(region_ids[0], periods[0] if periods else None, q, trace)
                return self._part("no_match", q, answer=NO_MATCH, trace=trace)
            want = None
            if periods and "-Q" in periods[0] or (partial or "").startswith("Q"):
                want = "Choraklik"
            elif periods and "-M" in periods[0] or (partial or "").startswith("M"):
                want = "Oylik"
            elif periods and intent != "series":
                query += " yillik"
            q_tokens = self.s.catalog.expand(query)[0]
            hits = self.s.catalog.search(query, limit=6, periodicity=want)
            trace.append({"tool": "search_indicators", "args": {"query": query, "periodicity": want},
                          "status": "ok" if hits else "no_match"})
            if not hits:
                return self._part("no_match", q, answer=NO_MATCH, trace=trace)
            if hits[0].score < 0.42:
                return self._part("no_match", q, answer=NO_MATCH, trace=trace,
                                  alternatives=[h.as_dict() for h in hits[:3]])
            if intent == "growth" and len(periods) < 2:
                for h in self.s.catalog.search(query + " osish surati", limit=3, periodicity=want):
                    nm = normalize_text(h.name_uz)
                    if "osish" in nm and h.score >= 0.5:
                        rate_hit = h
                        break

        # candidates: the best few hits (a later hit may carry the requested region/period)
        cand = [h for h in hits if h.score >= hits[0].score - 0.12][:5]
        if rate_hit and intent == "growth" and not forced_ind:
            cand = [rate_hit] + [h for h in cand if h.indicator_id != rate_hit.indicator_id]
        first_failure = None
        for h in cand:
            part = self._execute(intent, h.indicator_id, region_ids, periods, partial, latest, forecast, q, trace, hits,
                                 volume_hit=(cand[1] if rate_hit and len(cand) > 1 else None), rate_hit=rate_hit)
            if part["status"] == "ok":
                warn = self._warnings(h, cand[0], q_tokens, forced_ind)
                if warn:
                    part["answer"] += "\n\n" + " ".join(warn)
                    part["notes"] = (part.get("notes") or []) + warn
                return part
            first_failure = first_failure or part
            if part["status"] == "ambiguous":
                break
        return first_failure

    @staticmethod
    def _warnings(h, top, q_tokens, forced) -> list[str]:
        """Say so when the indicator does not fully cover the question (never answer silently)."""
        out = []
        if forced:
            return out
        if h.indicator_id != top.indicator_id:
            out.append("Eslatma: eng mos ko'rsatkichda so'ralgan hudud yoki davr bo'yicha ma'lumot yo'q edi, "
                       "shuning uchun yaqin ko'rsatkich keltirildi. Quyidagi boshqa ko'rsatkichlarni ham ko'rib chiqing.")
        skip = {"yillik", "choraklik", "oylik"}
        miss = [t for t in q_tokens if t not in getattr(h, "matched", []) and len(t) >= 4 and t not in skip]
        if miss:
            out.append(f"Eslatma: «{', '.join(miss[:3])}» tushunchasi ko'rsatkichda alohida ajratilmagan, "
                       "shuning uchun umumiy ko'rsatkich keltirildi.")
        return out

    # ---- intent executors
    def _call(self, trace, name, **kw):
        res = getattr(self.s, name)(**kw)
        trace.append({"tool": name, "args": {k: v for k, v in kw.items() if v is not None}, "status": res.get("status")})
        return res

    def _execute(self, intent, ind_id, region_ids, periods, partial, latest, forecast, q, trace, hits, volume_hit=None,
                 rate_hit=None):
        info = self.s.describe_indicator(ind_id)
        ind = {"id": info["indicator_id"], "code": info["code"], "name": info["name"], "unit": info["unit"],
               "periodicity": info["periodicity"], "is_cumulative": info["is_cumulative"],
               "range": info["period_range"]}
        alts = [h.as_dict() for h in hits if h.indicator_id != ind_id][:4]
        region = region_ids[0] if region_ids else None
        # period to use
        last = info["period_range"][1]
        period = periods[0] if periods else None
        if period is None:
            period = last
            if partial and last:
                y = last[:4]
                period = f"{y}-{partial}"
        # annual indicator asked for a quarter etc. is handled by the tools (no_data with ranges)
        rows = []
        if intent == "top":
            order = "lowest" if set(normalize_text(q).split()) & TOP_LOW else "highest"
            res = self._call(trace, "top_bottom", indicator_id=ind_id, period=period, order=order, limit=5)
            return self._finish_top(res, ind, alts, q, trace, order)
        if intent == "compare":
            if len(region_ids) >= 2:
                res = self._call(trace, "compare_regions", indicator_id=ind_id, regions=region_ids, period=period)
                return self._finish_compare(res, ind, alts, q, trace)
            res = self._call(trace, "top_bottom", indicator_id=ind_id, period=period, order="highest", limit=14)
            return self._finish_top(res, ind, alts, q, trace, "highest", title="Hududlar bo'yicha taqqoslash")
        if intent == "series" or (intent == "growth" and len(periods) >= 2 and False):
            start = periods[0] if len(periods) >= 2 else None
            end = periods[1] if len(periods) >= 2 else None
            res = self._call(trace, "get_series", indicator_id=ind_id, region=region, start=start, end=end)
            return self._finish_series(res, ind, alts, q, trace)
        if intent == "growth":
            if rate_hit and ind_id == rate_hit.indicator_id:  # official growth-rate indicator: read it, do not recompute
                res = self._call(trace, "get_value", indicator_id=ind_id, period=period, region=region)
                return self._finish_growth(res, ind, alts, q, trace, is_rate=True, region=region, period=period)
            if len(periods) >= 2:
                pf, pt = sorted(periods[:2], key=_sort_key)
                res = self._call(trace, "calc_change", indicator_id=ind_id, period_from=pf, period_to=pt,
                                 region=region)
            else:
                res = self._call(trace, "calc_growth", indicator_id=ind_id, period=period, region=region,
                                 versus="previous_year")
            return self._finish_growth(res, ind, alts, q, trace, is_rate=bool(rate_hit and ind_id == rate_hit.indicator_id),
                                       volume_hit=volume_hit, region=region, period=period)
        res = self._call(trace, "get_value", indicator_id=ind_id, period=period, region=region)
        return self._finish_value(res, ind, alts, q, trace, latest=(not periods))

    # ---- finishers
    def _spark(self, ind_id, member, highlight=None, limit=60):
        res = self.s.get_series(ind_id, member)
        if res["status"] != "ok":
            return None
        pts = res["points"][-limit:]
        return {"points": pts, "highlight": highlight}

    def _no_data(self, res, ind, alts, q, trace):
        status = res.get("status", "error")
        if status == "ambiguous":
            cands = res.get("candidates", [])
            return self._part("ambiguous", q, ind=ind, alternatives=alts, trace=trace,
                              answer="Qaysi birini nazarda tutyapsiz?",
                              clarify=[{"label": c["name"], "region": c["name"]} for c in cands[:6]])
        reason = _reason_uz(res)
        ap = res.get("available_periods") or {}
        rng = f" Mavjud davrlar: {ap.get('first')} – {ap.get('last')}." if ap.get("first") else ""
        if status == "not_found":
            avail = ", ".join(a["name"] for a in res.get("available", [])[:6])
            txt = (f"«{ind['name']}» ko'rsatkichida bu hudud bo'yicha ma'lumot yo'q."
                   + (f" Mavjud kesimlar: {avail}." if avail else ""))
        elif status == "refused":
            txt = f"Bu ko'rsatkich bo'yicha hisob-kitob qilib bo'lmaydi: {reason}."
        else:
            txt = (f"Kechirasiz, «{ind['name']}» bo'yicha so'ralgan ma'lumot bazada yo'q ({reason})." + rng
                   + " Men taxminiy yoki bashorat qilingan raqam keltirmayman.")
        st = "no_data" if status in ("no_data", "not_found", "incomplete", "error") else status
        return self._part(st, q, ind=ind, alternatives=alts, trace=trace, answer=txt)

    def _finish_value(self, res, ind, alts, q, trace, latest=False):
        if res["status"] != "ok":
            return self._no_data(res, ind, alts, q, trace)
        v, per, mem = res["value"], res["period"], res["member"]["name"]
        unit = f" {res['unit']}" if res.get("unit") else ""
        cum = " (yil boshidan jamlanma qiymat)" if ind["is_cumulative"] == 1 and "-Q" in per or ind["is_cumulative"] == 1 and "-M" in per else ""
        lead = "So'nggi mavjud ma'lumotga ko'ra, " if latest else ""
        text = f"{lead}**{period_human(per)}**da {mem} bo'yicha «{ind['name']}» — **{fmt(v)}**{unit}{cum}."
        card = {"type": "value", "title": ind["name"], "value": v, "value_text": fmt(v), "unit": res.get("unit"),
                "period": per, "period_text": period_human(per), "member": mem, "cumulative": ind["is_cumulative"] == 1,
                "spark": self._spark(ind["id"], res["member"]["id"], per)}
        return self._part("ok", q, ind=ind, alternatives=alts, trace=trace, answer=text, facts=res["facts"], cards=[card],
                          notes=res.get("notes"), source=res["source"], ignore=[ind["name"], mem, per],
                          followups=self._followups(ind, res["member"]["id"]), intent="value")

    def _finish_growth(self, res, ind, alts, q, trace, is_rate=False, volume_hit=None, region=None, period=None):
        if res["status"] != "ok":
            return self._no_data(res, ind, alts, q, trace)
        if "value" in res and "percent_change" not in res:  # official growth-rate indicator
            v, per, mem = res["value"], res["period"], res["member"]["name"]
            base_rel = "nisbatan" in normalize_text(res.get("unit") or "") and 50 < v < 200
            facts = list(res["facts"])
            derived = None
            if base_rel:
                derived = round(v - 100, 2)
                facts.append({"kind": "calc", "value": derived, "name": "growth_from_rate"})
            return self._finish_rate(res, ind, alts, q, trace, facts, derived)
        # computed change
        a, b = res["from"], res["to"]
        pct = res["percent_change"]
        unit = f" {res['unit']}" if res.get("unit") else ""
        verb = "oshgan" if res["direction"] == "increase" else "kamaygan" if res["direction"] == "decrease" else "o'zgarmagan"
        mem = res["member"]["name"]
        pct_txt = f"**{fmt(abs(pct))}%**ga {verb}" if pct is not None else "o'zgarishni foizda hisoblab bo'lmaydi"
        text = (f"{mem} bo'yicha «{ind['name']}» {period_human(a['period'])}dan {period_human(b['period'])}ga {pct_txt}: "
                f"{fmt(a['value'])} → {fmt(b['value'])}{unit} (mutlaq o'zgarish {fmt(res['absolute_change'])}).")
        card = {"type": "growth", "title": ind["name"], "from": {**a, "period_text": period_human(a["period"]), "text": fmt(a["value"])},
                "to": {**b, "period_text": period_human(b["period"]), "text": fmt(b["value"])},
                "percent": pct, "percent_text": None if pct is None else fmt(pct), "absolute": res["absolute_change"],
                "absolute_text": fmt(res["absolute_change"]), "direction": res["direction"], "unit": res.get("unit"),
                "member": mem, "spark": self._spark(ind["id"], res["member"]["id"], b["period"])}
        extra_cards, extra_facts = [], []
        return self._part("ok", q, ind=ind, alternatives=alts, trace=trace, answer=text, facts=res["facts"], cards=[card],
                          notes=res.get("notes"), source=res["source"], ignore=[ind["name"], mem, a["period"], b["period"]],
                          followups=self._followups(ind, res["member"]["id"]), intent="growth")

    def _finish_rate(self, res, ind, alts, q, trace, facts, derived):
        v, per, mem = res["value"], res["period"], res["member"]["name"]
        if derived is not None:
            verb = "oshgan" if derived > 0 else "kamaygan" if derived < 0 else "o'zgarmagan"
            text = (f"{period_human(per)}da {mem} bo'yicha «{ind['name']}» o'tgan yilga nisbatan **{fmt(v)}%** ni tashkil etdi, "
                    f"ya'ni **{fmt(abs(derived))}%**ga {verb} (rasmiy o'sish sur'ati ko'rsatkichi).")
        else:
            text = f"{period_human(per)}da {mem} bo'yicha «{ind['name']}» — **{fmt(v)}** {res.get('unit') or ''}.".replace("  ", " ")
        card = {"type": "growth_rate", "title": ind["name"], "rate": v, "rate_text": fmt(v), "growth": derived,
                "growth_text": None if derived is None else fmt(derived), "period": per, "period_text": period_human(per),
                "member": mem, "unit": res.get("unit"), "spark": self._spark(ind["id"], res["member"]["id"], per)}
        return self._part("ok", q, ind=ind, alternatives=alts, trace=trace, answer=text, facts=facts, cards=[card],
                          notes=res.get("notes"), source=res["source"], ignore=[ind["name"], mem, per],
                          followups=self._followups(ind, res["member"]["id"]), intent="growth")

    def _finish_series(self, res, ind, alts, q, trace):
        if res["status"] != "ok":
            return self._no_data(res, ind, alts, q, trace)
        pts, mem = res["points"], res["member"]["name"]
        first, last = pts[0], pts[-1]
        hi = max(pts, key=lambda p: p["value"])
        lo = min(pts, key=lambda p: p["value"])
        unit = f" {res['unit']}" if res.get("unit") else ""
        facts = list(res["facts"])
        text = (f"{mem} bo'yicha «{ind['name']}»: {period_human(first['period'])}da **{fmt(first['value'])}**, "
                f"{period_human(last['period'])}da **{fmt(last['value'])}**{unit}. "
                f"Eng yuqori qiymat — {fmt(hi['value'])} ({period_human(hi['period'])}), eng past — {fmt(lo['value'])} ({period_human(lo['period'])}).")
        if len(pts) >= 2 and first["value"] not in (0, None):
            ch = self.s.calc_change(ind["id"], first["period"], last["period"], res["member"]["id"])
            if ch["status"] == "ok" and ch["percent_change"] is not None:
                facts += [f for f in ch["facts"] if f["kind"] == "calc"]
                text += f" Umumiy o'zgarish: **{fmt(ch['percent_change'])}%**."
        card = {"type": "series", "title": ind["name"], "unit": res.get("unit"), "member": mem, "points": pts,
                "cumulative": ind["is_cumulative"] == 1,
                "stats": {"first": first, "last": last, "max": hi, "min": lo}}
        return self._part("ok", q, ind=ind, alternatives=alts, trace=trace, answer=text, facts=facts, cards=[card],
                          notes=res.get("notes"), source=res["source"],
                          ignore=[ind["name"], mem] + [p["period"] for p in pts],
                          followups=self._followups(ind, res["member"]["id"], skip="series"), intent="series")

    def _finish_compare(self, res, ind, alts, q, trace):
        if res["status"] != "ok":
            return self._no_data(res, ind, alts, q, trace)
        rk, unit = res["ranking"], f" {res['unit']}" if res.get("unit") else ""
        lines = "\n".join(f"- **{r['member']}** — {fmt(r['value'])}{unit}" for r in rk)
        ratio = res.get("ratio_highest_to_lowest")
        text = (f"{period_human(res['period'])}: «{ind['name']}» bo'yicha **{res['highest']['member']}** eng yuqori ({fmt(res['highest']['value'])}), "
                f"**{res['lowest']['member']}** eng past ({fmt(res['lowest']['value'])}). Farq: {fmt(res['difference_highest_lowest'])}"
                + (f", ya'ni {fmt(ratio)} marta." if ratio else ".") + "\n" + lines)
        card = {"type": "ranking", "title": "Hududlarni taqqoslash", "indicator": ind["name"], "unit": res.get("unit"),
                "period": res["period"], "period_text": period_human(res["period"]),
                "rows": [{"member": r["member"], "value": r["value"], "text": fmt(r["value"])} for r in rk]}
        return self._part("ok", q, ind=ind, alternatives=alts, trace=trace, answer=text, facts=res["facts"], cards=[card],
                          source=res["source"], ignore=[ind["name"], res["period"]] + [r["member"] for r in rk],
                          followups=self._followups(ind, 1, skip="compare"), intent="compare")

    def _finish_top(self, res, ind, alts, q, trace, order, title=None):
        if res["status"] != "ok":
            return self._no_data(res, ind, alts, q, trace)
        rk, unit = res["ranking"], f" {res['unit']}" if res.get("unit") else ""
        word = "eng yuqori" if order == "highest" else "eng past"
        lines = "\n".join(f"- **{r['member']}** — {fmt(r['value'])}{unit}" for r in rk)
        text = f"{period_human(res['period'])}: «{ind['name']}» bo'yicha {word} ko'rsatkichli hududlar:\n{lines}"
        card = {"type": "ranking", "title": title or ("Eng yuqori hududlar" if order == "highest" else "Eng past hududlar"),
                "indicator": ind["name"], "unit": res.get("unit"), "period": res["period"],
                "period_text": period_human(res["period"]),
                "rows": [{"member": r["member"], "value": r["value"], "text": fmt(r["value"])} for r in rk]}
        return self._part("ok", q, ind=ind, alternatives=alts, trace=trace, answer=text, facts=res["facts"], cards=[card],
                          source=res["source"], ignore=[ind["name"], res["period"]] + [r["member"] for r in rk],
                          followups=self._followups(ind, 1, skip="top"), intent="top")

    # ---- region profile: a vague "tell me about <region>" gets that region's headline indicators
    HEADLINES = [
        ("Aholi soni", "doimiy aholi soni jami"),
        ("Yalpi hududiy mahsulot", "yalpi ichki hududiy mahsulot hajmi yillik"),
        ("Ishsizlik darajasi", "ishsizlik darajasi"),
        ("O'rtacha oylik ish haqi", "ortacha hisoblangan oylik ish haqi yillik"),
        ("Asosiy kapitalga investitsiyalar", "asosiy kapitalga investitsiyalar hajmi yillik"),
        ("Eksport hajmi", "eksport hajmi yillik"),
        ("Sanoat ishlab chiqarish hajmi", "sanoat mahsulotlari ishlab chiqarish hajmi yillik"),
        ("Qishloq xo'jaligi mahsuloti", "qishloq xojaligi mahsuloti hajmi yillik"),
    ]

    def _profile(self, member_id: int, period, q, trace):
        mem = self.s.resolve_member(member_id)
        if mem["status"] != "ok":
            return self._part("no_match", q, answer=NO_MATCH, trace=trace)
        m = mem["member"]
        rows, facts, ignore = [], [], [m["name_uz"]]
        for label, query in self.HEADLINES:
            for h in self.s.catalog.search(query, limit=4, periodicity="Yillik"):
                if h.score < 0.55:
                    continue
                if m["id"] not in {x["id"] for x in self.s.indicator_members(h.indicator_id)}:
                    continue
                series = self.s._series(h.indicator_id, m["id"])
                if not series:
                    continue
                per = period if period and any(r["label"] == period for r in series) else series[-1]["label"]
                res = self.s.get_value(h.indicator_id, per, m["id"])
                trace.append({"tool": "get_value", "args": {"indicator_id": h.indicator_id, "period": per, "region": m["name_uz"]},
                              "status": res["status"]})
                if res["status"] != "ok":
                    continue
                rows.append({"label": label, "indicator_id": h.indicator_id, "indicator": h.name_uz, "unit": res.get("unit"),
                             "value": res["value"], "text": fmt(res["value"]), "period": per, "period_text": period_human(per)})
                facts += res["facts"]
                ignore += [h.name_uz, per]
                break
        if not rows:
            return self._part("no_match", q, answer=NO_MATCH, trace=trace)
        lines = "\n".join(f"- **{r['label']}** — {r['text']} {r['unit'] or ''} ({r['period_text']})".replace("  ", " ") for r in rows)
        text = f"**{m['name_uz']}** bo'yicha asosiy ko'rsatkichlar (so'nggi mavjud ma'lumot):\n{lines}"
        card = {"type": "profile", "title": f"{m['name_uz']} — asosiy ko'rsatkichlar", "member": m["name_uz"], "member_id": m["id"],
                "rows": rows}
        alts = []
        return self._part("ok", q, ind=None, alternatives=alts, trace=trace, answer=text, facts=facts, cards=[card],
                          source={"provider": "https://siat.stat.uz/", "license": "CC BY 4.0"}, ignore=ignore,
                          intent="profile")

    # ---- helpers
    def _followups(self, ind, member_id, skip=None):
        out = []
        if skip != "series":
            out.append({"label": "Dinamikasini ko'rsat", "question": "dinamikasi", "indicator_id": ind["id"]})
        if skip not in ("compare", "top"):
            out.append({"label": "Hududlar bo'yicha taqqosla", "question": "hududlarni solishtir",
                        "indicator_id": ind["id"]})
        return out[:3]

    def _part(self, status, q, ind=None, answer="", facts=None, cards=None, alternatives=None, clarify=None, trace=None,
              notes=None, source=None, ignore=None, followups=None, intent=None) -> dict:
        facts = facts or []
        part = {"status": status, "question": q, "intent": intent, "answer": answer, "indicator": ind, "cards": cards or [],
                "alternatives": alternatives or [], "clarify": clarify or [], "followups": followups or [],
                "notes": notes or [], "trace": trace or [], "facts": facts, "source": source}
        if status == "ok":
            ver = verify_answer(answer, facts, ignore)
            part["verification"] = ver
            if not ver["passed"]:  # never ship an unverified number
                part["status"] = "unverified"
                part["answer"] = ("Javobni bazadagi ma'lumotlar bilan tasdiqlab bo'lmadi, shuning uchun uni ko'rsatmayman. "
                                  "Savolni aniqroq qilib qayta yozing.")
                part["cards"] = []
        return part


def _reason_uz(res: dict) -> str:
    r = (res.get("reason") or res.get("message") or "").lower()
    if "cumulative" in r:
        return "ko'rsatkichning jamlanma yoki jamlanma emasligi hali aniqlanmagan"
    if "annual" in r or "quarterly/monthly" in r:
        return "bu ko'rsatkich faqat yillik beriladi"
    if "no full-year" in r:
        return "bu ko'rsatkich choraklik/oylik, to'liq yil qiymati yo'q"
    if "no value" in r or "empty" in r:
        return "manbada bu davr uchun qiymat bo'sh"
    return "so'ralgan davr yoki hudud uchun qiymat yo'q"


NO_MATCH = ("Bu savol bo'yicha SIAT katalogida mos ko'rsatkich topilmadi. Men faqat siat.stat.uz dagi rasmiy statistika "
            "(iqtisodiyot, ijtimoiy soha, ekologiya) bo'yicha javob beraman va taxminiy raqam keltirmayman.")
