"""Optional Gemini layer (stage 6): the LLM only *understands* the question.

It rewrites a free-form question (any language, typos, vague wording) into one or more canonical
Uzbek questions that the deterministic engine already knows how to answer. The LLM never sees the
database and never produces a number: values are still read by code and checked by `verify_answer`.
Any failure (no key, timeout, quota, bad JSON) returns None and the caller falls back to the rules.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from datetime import date

import httpx

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "8"))
COOLDOWN = 60.0  # seconds to skip the LLM after a failure, so a quota error doesn't slow every request

_cache: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()
_down_until = 0.0
_last_error = ""


def _key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def _model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()


def enabled() -> bool:
    return bool(_key()) and os.getenv("SIAT_USE_LLM", "1") != "0"


def status() -> dict:
    return {"enabled": enabled(), "model": _model() if enabled() else None,
            "cooling_down": time.time() < _down_until, "last_error": _last_error or None}


SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "language": {"type": "STRING", "description": "uz, ru, en or other"},
        "on_topic": {"type": "BOOLEAN", "description": "true only if the question asks for statistical data (numbers, indicators, regions, periods)"},
        "questions": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["language", "on_topic", "questions"],
}


def _prompt(region_names: list[str]) -> str:
    return f"""You rewrite user questions about official statistics of Uzbekistan (siat.stat.uz) into
canonical Uzbek (Latin script) questions for a search engine. Today is {date.today().isoformat()}.

Rules:
- Input may be Uzbek (Latin/Cyrillic), Russian, English, with typos, slang or vague wording. Detect the language.
- Output 1-3 independent questions in `questions`. Split only if the user clearly asked several things.
- Use the official Uzbek statistical terms, e.g.: yalpi ichki mahsulot, yalpi hududiy mahsulot, ishsizlik darajasi,
  doimiy aholi soni, o'rtacha oylik ish haqi, eksport hajmi, import hajmi, tashqi savdo aylanmasi,
  iste'mol narxlari indeksi, sanoat ishlab chiqarish hajmi, qishloq xo'jaligi, investitsiyalar, tug'ilish, o'lim,
  nikohlar soni, nikohdan ajralishlar soni, tug'ilganlar soni, o'lganlar soni, o'rtacha umr ko'rish davomiyligi.
- Replace colloquial, wrong or dialect words with the standard statistical term, even if the user's word is
  not in the list (e.g. "ajrimlar" -> "nikohdan ajralishlar soni", "pul maosh" -> "o'rtacha oylik ish haqi",
  "bekor odamlar" -> "ishsizlik darajasi", "kopayish" -> "o'sish").
- Region names exactly as: {", ".join(region_names)}. Write "O'zbekiston" for the whole country.
  Never guess a region the user did not mention. If the user says just "Toshkent", keep it as "Toshkent".
- Keep years as digits ("2024 yilda"), quarters as "2-chorak", months as Uzbek names (mart, aprel...).
  Resolve relative words ("last year", "o'tgan yil") using today's date.
- Keep the intent in the question: "necha foiz o'sdi", "dinamikasi", "solishtir", "eng yuqori", "eng past".
- If a region is mentioned but no specific indicator, write "<region> haqida ma'lumot ber".
- Never add information the user did not write: no extra region, no "O'zbekiston Respublikasi", no year. Keep "Toshkent"
  exactly as "Toshkent" unless the user wrote shahri/viloyati/city/oblast (the engine will ask the user which one).
- Set on_topic=false for anything that is not a request for statistical numbers (politics, weather, prices of
  crypto, chit-chat, general knowledge, "who is...", restaurants). Then return the question translated as-is.
- Do NOT answer, do NOT invent numbers or indicators. If the text is not about statistics, return it translated
  to Uzbek unchanged in meaning (the engine will refuse it)."""


def rewrite(question: str, region_names: list[str]) -> dict | None:
    """-> {"language": "ru", "questions": ["..."]} or None when the LLM is unavailable."""
    global _down_until, _last_error
    if not enabled() or time.time() < _down_until:
        return None
    ck = question.strip().lower()
    with _lock:
        if ck in _cache:
            _cache.move_to_end(ck)
            return _cache[ck]
    gen = {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA,
           "maxOutputTokens": 400}
    if "2.5" in _model():
        gen["thinkingConfig"] = {"thinkingBudget": 0}  # no hidden reasoning: much faster, enough for rewriting
    body = {"systemInstruction": {"parts": [{"text": _prompt(region_names)}]},
            "contents": [{"role": "user", "parts": [{"text": question[:600]}]}], "generationConfig": gen}
    try:
        r = httpx.post(API.format(model=_model()), json=body, timeout=TIMEOUT,
                       headers={"x-goog-api-key": _key(), "Content-Type": "application/json"})
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text)
        qs = [q.strip() for q in data.get("questions", []) if isinstance(q, str) and q.strip()][:3]
        if not qs:
            raise ValueError("empty rewrite")
        out = {"language": str(data.get("language", "?"))[:8], "questions": qs,
               "on_topic": data.get("on_topic", True) is not False}
    except Exception as e:  # network, quota, timeout, bad JSON... -> fall back to rules
        _last_error = f"{type(e).__name__}: {str(e)[:120]}".replace(_key(), "***")
        _down_until = time.time() + COOLDOWN
        return None
    with _lock:
        _cache[ck] = out
        if len(_cache) > 500:
            _cache.popitem(last=False)
    return out
