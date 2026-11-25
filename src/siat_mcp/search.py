"""Stage 4: indicator search over the 3261-indicator catalog (in memory, IDF-weighted fuzzy tokens).

The catalog is small, so no vector index is needed: each indicator is a bag of normalized
tokens (name_uz + name_uzc + name_en + category path). Query tokens match document tokens
by exact/prefix similarity (Uzbek suffixes), weighted by IDF. Synonyms expand the query.
"""
from __future__ import annotations

import math
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from .normalize import normalize_text, token_match, tokens

STOPWORDS = set(
    """va bilan uchun qancha necha nima edi bor boladi bolgan haqida menga ayt ayting korsat korsating
    yil yilda yilgi yilning yilga qaysi qanday boyicha ham yoki esa bu shu u ning dan da ga ni the of in
    and for what is how much many ma lumot malumot malumotlar statistika korsatkich korsatkichi
    mamlakat mamlakatda respublika respublikada respublikasi ozbekiston ozbekistonda viloyat viloyatda
    oy oyida chorak choragi choragida kesimida kesimi dinamikasi""".split()
)
PERIOD_WORDS = {"yillik": "Yillik", "yillikda": "Yillik", "choraklik": "Choraklik", "oylik": "Oylik"}
# (query phrase -> extra tokens added to the query); keys are in normalized form
SYNONYMS = {
    "yaim": "yalpi ichki mahsulot", "ymm": "yalpi ichki mahsulot", "gdp": "yalpi ichki mahsulot",
    "yhm": "yalpi hududiy mahsulot", "ichki mahsulot": "yalpi", "yalpi mahsulot": "ichki",
    "maosh": "ish haqi", "oylik maosh": "ish haqi", "ish haqi": "ish haqi",
    "inflyatsiya": "iste mol narxlari indeksi", "inflatsiya": "iste mol narxlari indeksi",
    "narx": "narxlari", "ishsizlik": "ishsizlik darajasi", "aholi": "aholi soni",
    "tugilish": "tugilganlar", "olim": "vafot etganlar", "eksport": "eksport", "import": "import",
    "savdo": "savdo", "soliq": "soliqlar", "investitsiya": "investitsiyalar",
    "turizm": "turistlar", "yol": "avtomobil yollari", "ish orinlari": "ish orni",
}


@dataclass
class Hit:
    indicator_id: int
    code: str
    name_uz: str
    periodicity: str
    unit: str | None
    category_path: str
    score: float
    first_period: str | None = None
    last_period: str | None = None
    is_cumulative: int | None = None
    matched: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "indicator_id": self.indicator_id, "code": self.code, "name": self.name_uz,
            "periodicity": self.periodicity, "unit": self.unit, "category": self.category_path,
            "period_range": [self.first_period, self.last_period],
            "is_cumulative": self.is_cumulative, "score": round(self.score, 3),
        }


class Catalog:
    def __init__(self, db: sqlite3.Connection):
        self.docs: list[dict] = []
        cat = {}
        for cid, pid, name in db.execute("SELECT id, parent_id, name_uz FROM categories"):
            cat[cid] = (pid, name)

        def path(cid):
            parts = []
            while cid in cat:
                pid, name = cat[cid]
                parts.append(name)
                cid = pid
            return " > ".join(reversed(parts))

        df: Counter[str] = Counter()
        rows = db.execute(
            "SELECT id, code, category_id, name_uz, name_uzc, name_en, periodicity, unit_uz, "
            "first_period, last_period, is_cumulative FROM indicators"
        ).fetchall()
        for (iid, code, cid, uz, uzc, en, per, unit, fp, lp, cum) in rows:
            name_toks = tokens(uz) or tokens(uzc)
            extra = set(tokens(uzc)) | set(tokens(en)) | set(tokens(path(cid)))
            doc_tokens = [t for t in name_toks if t not in STOPWORDS]
            extra = {t for t in extra if t not in STOPWORDS} - set(doc_tokens)
            self.docs.append({
                "id": iid, "code": code, "name": uz or uzc or en, "period": per, "unit": unit,
                "cat": path(cid), "name_toks": doc_tokens, "extra_toks": sorted(extra),
                "fp": fp, "lp": lp, "cum": cum,
            })
            df.update(set(doc_tokens) | extra)
        n = len(self.docs)
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        self.default_idf = math.log(n + 1)

    def _idf(self, t: str) -> float:
        return self.idf.get(t, self.default_idf)

    def expand(self, query: str) -> tuple[list[str], str | None]:
        """-> (query tokens incl. synonym expansion, requested periodicity or None)."""
        norm = normalize_text(query)
        words = [t for t in norm.split() if t not in STOPWORDS and not t.isdigit()]
        wanted = None
        for w in list(words):
            if w in PERIOD_WORDS:
                wanted = PERIOD_WORDS[w]
                words.remove(w)
        q, extra = list(words), []
        for phrase, add in SYNONYMS.items():
            if f" {phrase} " in f" {norm} ":
                if phrase in q and phrase not in add.split():
                    q.remove(phrase)  # the acronym itself never appears in names
                extra += add.split()
        return q + [t for t in dict.fromkeys(extra) if t not in q], wanted

    def search(self, query: str, limit: int = 8, periodicity: str | None = None,
               min_score: float = 0.35) -> list[Hit]:
        q, wanted = self.expand(query)
        if "oylik" in normalize_text(query).split() and "ish haqi" in normalize_text(query):
            wanted = None  # "oylik ish haqi" = monthly wage, not monthly periodicity
            q = [t for t in normalize_text(query).split() if t not in STOPWORDS and not t.isdigit()]
        if not q:
            return []
        hits = []
        for d in self.docs:
            if periodicity and d["period"] != periodicity:
                continue
            matched_idf = q_idf = 0.0
            doc_matched = {}
            matched_words = []
            for t in q:
                w = self._idf(t)
                q_idf += w
                best, best_dt = 0.0, None
                for dt in d["name_toks"]:
                    s = token_match(t, dt)
                    if s > best:
                        best, best_dt = s, dt
                        if s == 1.0:
                            break
                if best < 0.75:  # fall back to secondary text (translit uzc, english, category path)
                    for dt in d["extra_toks"]:
                        s = token_match(t, dt) * 0.8
                        if s > best:
                            best, best_dt = s, dt
                if best >= 0.6:
                    matched_idf += w * best
                    matched_words.append(t)
                    if best_dt:
                        doc_matched[best_dt] = max(doc_matched.get(best_dt, 0), best)
            if not matched_words:
                continue
            coverage = matched_idf / q_idf
            doc_idf = sum(self._idf(t) for t in d["name_toks"]) or 1.0
            precision = sum(self._idf(t) * s for t, s in doc_matched.items() if t in d["name_toks"]) / doc_idf
            score = 0.75 * coverage + 0.25 * min(1.0, precision)
            if wanted:
                score += 0.12 if d["period"] == wanted else -0.12
            if score >= min_score:
                hits.append(Hit(d["id"], d["code"], d["name"], d["period"], d["unit"], d["cat"], score,
                                d["fp"], d["lp"], d["cum"], matched_words))
        hits.sort(key=lambda h: (-h.score, len(h.name_uz)))
        return hits[:limit]
