"""Stage 3: text normalization (Uzbek Latin / Cyrillic / mixed -> one canonical form),
period expressions and region names.

Canonical text form: lowercase Latin, apostrophes/modifier letters removed
(o'zbekiston -> ozbekiston), Cyrillic transliterated, Cyrillic look-alikes inside Latin
words fixed ("vа bаliq" -> "va baliq").
"""
from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------- characters
APOSTROPHES = "ʻʼ‘’`´'′ʹ‛ʽ"
_APOS_TABLE = {ord(c): None for c in APOSTROPHES}

CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "o", "ғ": "g", "қ": "q", "ҳ": "h", "і": "i", "ї": "i", "ј": "y",
}
# Cyrillic letters that look like Latin ones; used when they sit inside an otherwise Latin word.
HOMOGLYPH = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "к": "k",
             "м": "m", "т": "t", "і": "i", "ј": "j"}

_WORD_RE = re.compile(r"[^\W_]+")


def _is_cyr(ch: str) -> bool:
    return "Ѐ" <= ch <= "ӿ"


def _fix_word(w: str) -> str:
    has_cyr = any(_is_cyr(c) for c in w)
    if not has_cyr:
        return w
    has_lat = any(c.isascii() and c.isalpha() for c in w)
    if has_lat:  # mixed script word: keep Latin, map look-alikes, transliterate the rest
        return "".join(HOMOGLYPH.get(c) or CYR2LAT.get(c, c) if _is_cyr(c) else c for c in w)
    return "".join(CYR2LAT.get(c, c) for c in w)


def normalize_text(s: str | None) -> str:
    """Canonical lowercase Latin form used for every comparison in the system."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower().replace("\xa0", " ").translate(_APOS_TABLE)
    return " ".join(_fix_word(w) for w in _WORD_RE.findall(s))


def tokens(s: str | None) -> list[str]:
    return normalize_text(s).split()


# --------------------------------------------------------------------------- fuzzy token match
def token_match(a: str, b: str) -> float:
    """Similarity of two normalized tokens in [0,1]; handles Uzbek suffixes by prefix overlap."""
    if a == b:
        return 1.0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 4 and long_.startswith(short) and len(long_) - len(short) <= 5:
        return 0.9
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    if n >= 5 and n >= 0.75 * len(short):
        return 0.75
    return 0.0


# --------------------------------------------------------------------------- periods
MONTHS = {
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6, "iyul": 7, "avgust": 8,
    "sentabr": 9, "sentyabr": 9, "oktabr": 10, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
}
ORDINALS = {"birinchi": 1, "ikkinchi": 2, "uchinchi": 3, "tortinchi": 4, "tortinchi": 4,
            "perviy": 1, "pervyy": 1}
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_Q_PATTERNS = [
    re.compile(r"\b(\d{4}) ?q ?([1-4])\b"),                     # 2023 q2, 2023q2
    re.compile(r"\b([1-4]) ?(?:chorak|kvartal|choragi|chorakda)"),
    re.compile(r"\b(birinchi|ikkinchi|uchinchi|tortinchi) (?:chorak|kvartal)"),
    re.compile(r"\b(i{1,3}|iv) (?:chorak|kvartal)"),
    re.compile(r"\bq ?([1-4])\b"),
]


def parse_period_expr(text: str) -> dict:
    """Parse '2023', '2023-Q2', '2023 yil 2-chorak', 'II chorak 2023', 'mart 2024', '2023-M03'...

    Returns {"year", "quarter", "month", "label"}; label is canonical (2023 / 2023-Q2 / 2023-M03)
    when it can be formed, else None.
    """
    t = normalize_text(text)
    year = quarter = month = None
    m = re.search(r"\b((?:19|20)\d{2})\b", t)
    if m:
        year = int(m.group(1))
    m = re.search(r"\b(?:19|20)\d{2} m(\d{1,2})\b", t)  # canonical 2023-M03
    if m and 1 <= int(m.group(1)) <= 12:
        month = int(m.group(1))
    if month is None:
        for w in t.split():
            for name, num in MONTHS.items():
                if w.startswith(name) and len(w) - len(name) <= 4:
                    month = num
                    break
            if month:
                break
    if month is None:
        for pat in _Q_PATTERNS:
            q = pat.search(t)
            if q:
                g = q.group(q.lastindex)
                quarter = int(g) if g.isdigit() else ORDINALS.get(g) or ROMAN.get(g)
                if q.lastindex == 2 and q.re is _Q_PATTERNS[0]:
                    year = int(q.group(1))
                break
    label = None
    if year and month:
        label = f"{year}-M{month:02d}"
    elif year and quarter:
        label = f"{year}-Q{quarter}"
    elif year:
        label = str(year)
    return {"year": year, "quarter": quarter, "month": month, "label": label}


# --------------------------------------------------------------------------- regions
REGION_ALIASES = {
    "respublika": 1, "ozbekiston": 1, "ozbekiston respublikasi": 1, "mamlakat": 1,
    "butun mamlakat": 1, "uzbekistan": 1, "uzbekistan republic": 1, "republic of uzbekistan": 1,
    "qoraqalpogiston": 2, "qoraqalpoq": 2, "qoraqalpogiston respublikasi": 2, "karakalpakstan": 2,
    "toshkent shahri": 3, "toshkent shahar": 3, "poytaxt": 3, "tashkent city": 3,
    "toshkent viloyati": 4, "tashkent region": 4,
}
_REGION_STOP = {"viloyati", "viloyat", "shahri", "shahar", "respublikasi", "respublika", "region", "city", "oblast"}


def region_key(name: str) -> str:
    """Name without administrative words: 'Samarqand viloyati' -> 'samarqand'."""
    return " ".join(w for w in normalize_text(name).split() if w not in _REGION_STOP)
