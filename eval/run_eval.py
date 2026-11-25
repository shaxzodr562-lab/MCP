"""Accuracy evaluation of the SIAT assistant.

Ground truth is NOT taken from the system's own output: each case lists the indicator ids that are
acceptable answers (chosen by a human from the catalog) and the (region, period) cells that must be
reported; the expected numbers are read straight from the observations table.

A case is CORRECT only if status == ok, the indicator is acceptable, and every expected cell appears in
the answer facts with exactly the database value. Refusal cases are correct if the system refuses.

Run:  python eval/run_eval.py rules      # deterministic engine only
      python eval/run_eval.py gemini     # Gemini understanding + rules
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

mode = sys.argv[1] if len(sys.argv) > 1 else "rules"
os.environ["SIAT_USE_LLM"] = "1" if mode == "gemini" else "0"

import siat_mcp.config  # noqa: E402,F401  (loads .env)
from siat_mcp import llm  # noqa: E402
from siat_mcp.agent import Agent  # noqa: E402
from siat_mcp.queries import Stats  # noqa: E402

UZ, TSH, AND, BUX, FAR, JIZ, XOR, NAM, NAV, QAS, SAM, SUR = 1, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15
UNEMP, POP, BIRTH, DIV, GDP, GRP, GDPG, EXP, IMP, AGRI = [1875], [246], [223], [230, 2433], [1920], [1299], [1921], [2360, 1142], [2361, 2763, 1149], [1439]


def V(ids, *cells):
    return {"ids": ids, "cells": list(cells)}


# (category, question, expectation)
#   {"ids": [...], "cells": [(member_id, "period"), ...]} | {"refuse": True} | {"ambiguous": True} | {"profile": member_id}
CASES = [
    ("oddiy", "2023 yilda yalpi ichki mahsulot hajmi qancha edi?", V(GDP, (UZ, "2023"))),
    ("oddiy", "2024 yilda ishsizlik darajasi qancha?", V(UNEMP, (UZ, "2024"))),
    ("oddiy", "2022 yilda doimiy aholi soni qancha?", V(POP, (UZ, "2022"))),
    ("oddiy", "2023 yilda tug'ilganlar soni qancha?", V(BIRTH, (UZ, "2023"))),
    ("oddiy", "2024 yilda nikohdan ajralishlar soni qancha?", V(DIV, (UZ, "2024"))),
    ("oddiy", "2024 yilda eksport hajmi qancha bo'lgan?", V(EXP, (UZ, "2024"))),
    ("oddiy", "2023 yilda import hajmi qancha?", V(IMP, (UZ, "2023"))),
    ("oddiy", "2022 yilda qishloq xo'jaligi mahsulotlari umumiy hajmi qancha?", V(AGRI, (UZ, "2022"))),
    ("oddiy", "2024 yilda YaIM necha foiz o'sdi?", V(GDPG, (UZ, "2024"))),
    ("oddiy", "yaim 2022", V(GDP, (UZ, "2022"))),
    ("hudud", "Samarqand viloyati 2024 yil yalpi hududiy mahsulot qancha?", V(GRP, (SAM, "2024"))),
    ("hudud", "Buxoro viloyatida 2023 yilda ishsizlik darajasi qancha?", V(UNEMP, (BUX, "2023"))),
    ("hudud", "Xorazm viloyati aholi soni 2024", V(POP, (XOR, "2024"))),
    ("hudud", "Farg'ona viloyatida 2022 yilda tug'ilganlar soni qancha?", V(BIRTH, (FAR, "2022"))),
    ("hudud", "Navoiy viloyati 2023 yil eksport hajmi qancha?", V(EXP, (NAV, "2023"))),
    ("hudud", "Qashqadaryo viloyatida 2024 yilda import hajmi qancha?", V(IMP, (QAS, "2024"))),
    ("hudud", "Andijon viloyati 2023 yil qishloq xo'jaligi mahsulotlari hajmi", V(AGRI, (AND, "2023"))),
    ("hudud", "Toshkent shahri 2024 yil yalpi hududiy mahsulot", V(GRP, (TSH, "2024"))),
    ("hudud", "Surxondaryo viloyati ishsizlik darajasi 2022", V(UNEMP, (SUR, "2022"))),
    ("hudud", "Namangan viloyatida nikohdan ajralishlar soni 2023", V(DIV, (NAM, "2023"))),
    ("erkin/imlo", "samarqandda ishsizlik qancha 2024 yil", V(UNEMP, (SAM, "2024"))),
    ("erkin/imlo", "buxoroda aholi soni necha kishi 2023", V(POP, (BUX, "2023"))),
    ("erkin/imlo", "yalpi ichki mahsulot 2023 yil hajmi qnaca", V(GDP, (UZ, "2023"))),
    ("erkin/imlo", "ishsizlik darjasi Xorazmda 2022", V(UNEMP, (XOR, "2022"))),
    ("erkin/imlo", "2024 da eksport qancha bo'ldi O'zbekistonda", V(EXP, (UZ, "2024"))),
    ("erkin/imlo", "nechta bola tug'ilgan 2023 yilda", V(BIRTH, (UZ, "2023"))),
    ("erkin/imlo", "Andijonda 2024 yil necha kishi yashaydi", V(POP, (AND, "2024"))),
    ("erkin/imlo", "qancha odam ishsiz Jizzaxda 2023", V(UNEMP, (JIZ, "2023"))),
    ("kirill", "Ўзбекистонда 2023 йилда ялпи ички маҳсулот ҳажми қанча?", V(GDP, (UZ, "2023"))),
    ("kirill", "Самарқанд вилояти 2024 йил ишсизлик даражаси", V(UNEMP, (SAM, "2024"))),
    ("kirill", "Бухоро вилояти доимий аҳоли сони 2023", V(POP, (BUX, "2023"))),
    ("kirill", "2024 йилда экспорт ҳажми", V(EXP, (UZ, "2024"))),
    ("rus", "Какой был ВВП Узбекистана в 2023 году?", V(GDP, (UZ, "2023"))),
    ("rus", "Уровень безработицы в Самаркандской области в 2024 году", V(UNEMP, (SAM, "2024"))),
    ("rus", "Численность населения Бухарской области в 2023 году", V(POP, (BUX, "2023"))),
    ("rus", "Объем экспорта Узбекистана в 2024 году", V(EXP, (UZ, "2024"))),
    ("rus", "Сколько детей родилось в Узбекистане в 2023 году?", V(BIRTH, (UZ, "2023"))),
    ("rus", "Сколько разводов было в Узбекистане в 2024 году?", V(DIV, (UZ, "2024"))),
    ("ingliz", "What was Uzbekistan's GDP in 2023?", V(GDP, (UZ, "2023"))),
    ("ingliz", "Unemployment rate in Samarkand region in 2024", V(UNEMP, (SAM, "2024"))),
    ("ingliz", "How many people lived in Bukhara region in 2023?", V(POP, (BUX, "2023"))),
    ("ingliz", "Uzbekistan exports in 2024", V(EXP, (UZ, "2024"))),
    ("ingliz", "number of births in Uzbekistan in 2023", V(BIRTH, (UZ, "2023"))),
    ("ingliz", "GDP growth rate of Uzbekistan in 2024", V(GDPG, (UZ, "2024"))),
    ("taqqoslash", "Samarqand va Buxoroning 2024 yil yalpi hududiy mahsulotini solishtir", V(GRP, (SAM, "2024"), (BUX, "2024"))),
    ("taqqoslash", "2023 yilda Andijon va Namangan viloyatlarida ishsizlik darajasini taqqosla", V(UNEMP, (AND, "2023"), (NAM, "2023"))),
    ("taqqoslash", "Compare population of Samarkand and Bukhara in 2024", V(POP, (SAM, "2024"), (BUX, "2024"))),
    ("taqqoslash", "Сравни уровень безработицы в Самаркандской и Бухарской области в 2023", V(UNEMP, (SAM, "2023"), (BUX, "2023"))),
    ("dinamika", "2020 dan 2024 gacha eksport hajmi qancha o'zgargan?", V(EXP, (UZ, "2020"), (UZ, "2024"))),
    ("dinamika", "doimiy aholi soni 2015 va 2025 yillarda qanday o'zgargan?", V(POP, (UZ, "2015"), (UZ, "2025"))),
    ("noaniq", "Buxoro haqida ma'lumot ber", {"profile": BUX}),
    ("noaniq", "qandaydir samarqandga aloqador statistika chiqarchi", {"profile": SAM}),
    ("noaniq", "Toshkent aholi soni qancha 2023", {"ambiguous": True}),
    ("rad", "2031 yilda ishsizlik qancha bo'ladi?", {"refuse": True}),
    ("rad", "2030 yilda YaIM qancha bo'ladi?", {"refuse": True}),
    ("rad", "Samarqand viloyatida 1950 yilda aholi soni qancha edi?", {"refuse": True}),
    ("rad", "bitcoin narxi qancha?", {"refuse": True}),
    ("rad", "Bugun ob-havo qanday?", {"refuse": True}),
    ("rad", "Prezident kim?", {"refuse": True}),
    ("rad", "What will unemployment be in 2035?", {"refuse": True}),
    ("rad", "Какой будет ВВП в 2040 году?", {"refuse": True}),
    ("rad", "2027 yilda aholi soni qancha bo'ladi?", {"refuse": True}),
    ("rad", "Eng yaxshi restoran qayerda?", {"refuse": True}),
    # held-out: written after the first run, to check that the fixes generalise
    ("yangi", "O'zbekiston poytaxti qayer?", {"refuse": True}),
    ("yangi", "Who won the 2022 World Cup?", {"refuse": True}),
    ("yangi", "Как дела?", {"refuse": True}),
    ("yangi", "Toshkent ishsizlik darajasi 2024", {"ambiguous": True}),
    ("yangi", "Ташкент 2024 безработица", {"ambiguous": True}),
    ("yangi", "Qoraqalpog'iston aholi soni 2024", V(POP, (2, "2024"))),
    ("yangi", "Jizzax 2023 yalpi hududiy mahsulot", V(GRP, (JIZ, "2023"))),
    ("yangi", "Surxondaryo viloyatida tug'ilganlar soni 2024", V(BIRTH, (SUR, "2024"))),
]
root = Path(__file__).resolve().parents[1]
stats = Stats()
agent = Agent(stats)
db = stats.db


def truth(ind, member, period):
    r = db.execute("SELECT o.value FROM observations o JOIN periods p ON p.id=o.period_id "
                   "WHERE o.indicator_id=? AND o.member_id=? AND p.label=?", (ind, member, period)).fetchone()
    return r["value"] if r else None


def judge(exp, r):
    p = r["parts"][0]
    st = p["status"]
    if exp.get("refuse"):
        return st != "ok" and not p.get("cards"), f"status={st}"
    if exp.get("ambiguous"):
        return st == "ambiguous", f"status={st}"
    if exp.get("profile"):
        cards = p.get("cards") or []
        name = db.execute("SELECT name_uz FROM members WHERE id=?", (exp["profile"],)).fetchone()["name_uz"]
        ok = st == "ok" and cards and cards[0].get("type") == "profile" and name == cards[0].get("member")
        return bool(ok), f"status={st}"
    if st != "ok":
        return False, f"status={st}"
    ind = (p.get("indicator") or {}).get("id")
    if ind not in exp["ids"]:
        return False, f"wrong indicator {ind}: {(p.get('indicator') or {}).get('name')}"
    vals = [f for f in p.get("facts", []) if f.get("kind") == "value"]
    for member, period in exp["cells"]:
        t = truth(ind, member, period)
        mname = db.execute("SELECT name_uz FROM members WHERE id=?", (member,)).fetchone()["name_uz"]
        if t is None:
            return False, f"no truth for {ind}/{member}/{period}"
        if not any(abs(f["value"] - t) < 1e-6 and f.get("period") == period and f.get("member") == mname for f in vals):
            got = [(f.get("member"), f.get("period"), f["value"]) for f in vals][:3]
            return False, f"expected {t} for {mname} {period}, got {got}"
    if not p.get("verification", {}).get("passed", True):
        return False, "verifier failed"
    return True, "ok"


rows, by_cat, fallbacks = [], defaultdict(lambda: [0, 0]), 0
for cat, q, exp in CASES:
    llm._down_until = 0.0  # a transient failure must not silently switch off the LLM for the rest of the run
    t0 = time.time()
    r = agent.ask(q)
    if mode == "gemini" and r.get("engine") != "gemini+rules":  # one retry (free-tier hiccups)
        time.sleep(2)
        llm._down_until = 0.0
        r = agent.ask(q)
    if mode == "gemini" and r.get("engine") != "gemini+rules":
        fallbacks += 1
    ok, why = judge(exp, r)
    by_cat[cat][0] += ok
    by_cat[cat][1] += 1
    rows.append({"cat": cat, "q": q, "ok": bool(ok), "why": why, "engine": r.get("engine"),
                 "ms": round((time.time() - t0) * 1000), "interpreted_as": r["parts"][0].get("interpreted_as")})
    print(("PASS" if ok else "FAIL"), cat, "|", q[:60].encode("ascii", "replace").decode(), "|",
          why[:110].encode("ascii", "replace").decode())
    if mode == "gemini":
        time.sleep(0.4)

n = len(rows)
good = sum(r["ok"] for r in rows)
print(f"\nMODE={mode}  {good}/{n} = {100 * good / n:.1f}%  (LLM fallbacks: {fallbacks})")
for c, (g, t) in by_cat.items():
    print(f"  {c:12s} {g}/{t}  {100 * g / t:.0f}%")
out = root / "eval" / f"result_{mode}.json"
out.write_text(json.dumps({"mode": mode, "correct": good, "total": n, "fallbacks": fallbacks, "cases": rows},
                          ensure_ascii=False, indent=1), encoding="utf-8")
