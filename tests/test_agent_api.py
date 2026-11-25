import pytest

from siat_mcp.agent import fmt, verify_answer
from siat_mcp.config import settings

needs_db = pytest.mark.skipif(not settings.db_path.exists(), reason="run the ETL first")


def test_fmt_uzbek_numbers():
    assert fmt(1433975.7) == "1 433 975,7"
    assert fmt(141011.0) == "141 011"
    assert fmt(-3.25) == "-3,25"


def test_verifier_accepts_only_known_numbers():
    facts = [{"kind": "value", "value": 1433975.7}, {"kind": "calc", "value": 22.36}]
    assert verify_answer("2024-yil: **1 433 975,7** va **22,36%**", facts)["passed"]
    bad = verify_answer("2024-yil: **1 433 975,8**", facts)
    assert not bad["passed"] and bad["unmatched"] == ["1 433 975,8"]
    assert verify_answer("o'sish **22,4%**", facts)["passed"]            # correct rounding of 22.36 is fine
    assert verify_answer("o'sish **22,5%**", facts)["passed"] is False   # a wrong number is rejected


@pytest.fixture(scope="module")
def agent():
    from siat_mcp.agent import Agent
    from siat_mcp.queries import Stats
    return Agent(Stats())


def part(agent, q, **kw):
    return agent.ask(q, **kw)["parts"][0]


@needs_db
def test_official_growth_rate_is_read_not_recomputed(agent):
    p = part(agent, "2024 yilda O'zbekiston YaIM necha foiz o'sdi?")
    assert p["status"] == "ok" and p["cards"][0]["type"] == "growth_rate"
    assert p["cards"][0]["rate"] == 106.7 and p["cards"][0]["growth"] == 6.7
    assert p["verification"]["passed"]


@needs_db
def test_refuses_forecast_and_out_of_scope(agent):
    p = part(agent, "2031 yilda ishsizlik qancha bo'ladi?")
    assert p["status"] == "no_data" and not p["cards"]
    assert part(agent, "bitcoin narxi qancha")["status"] == "no_match"


@needs_db
def test_ambiguous_toshkent_asks_user(agent):
    p = part(agent, "Toshkent aholi soni qancha")
    assert p["status"] == "ambiguous" and len(p["clarify"]) == 2
    assert part(agent, "Toshkent aholi soni qancha", region="Toshkent shahri")["status"] == "ok"


@needs_db
def test_cyrillic_question_and_multi_question(agent):
    assert part(agent, "ўртача ойлик иш ҳақи 2025 2-чорак")["status"] == "ok"
    parts = agent.ask("2023 yil YaIM hajmi qancha? doimiy aholi soni dinamikasi")["parts"]
    assert len(parts) == 2 and all(p["status"] == "ok" for p in parts)


@needs_db
def test_api_endpoints():
    from fastapi.testclient import TestClient
    from siat_mcp.api import app
    c = TestClient(app)
    assert c.get("/api/health").json()["status"] == "ok"
    r = c.post("/api/ask", json={"question": "2023 yilda YaIM qancha edi?"}).json()
    assert r["parts"][0]["status"] == "ok"
    assert c.post("/api/ask", json={"question": ""}).status_code == 422
    assert c.get("/api/indicator/999999").status_code == 404
    assert c.get("/").status_code == 200


@needs_db
def test_vague_region_question_gets_a_verified_profile(agent):
    p = part(agent, "qandaydir buxoroga aloqador statistika chiqarchi")
    assert p["status"] == "ok" and p["intent"] == "profile"
    rows = p["cards"][0]["rows"]
    assert len(rows) >= 5 and p["cards"][0]["member"] == "Buxoro viloyati"
    assert p["verification"]["passed"] and p["verification"]["checked"] >= len(rows)
    assert part(agent, "bitcoin narxi qancha")["status"] == "no_match"  # no region -> still refuses


@needs_db
def test_llm_rewrite_is_used_and_numbers_still_come_from_db(agent, monkeypatch):
    from siat_mcp import llm
    monkeypatch.setenv("SIAT_USE_LLM", "1"); monkeypatch.setenv("GEMINI_API_KEY", "x"); monkeypatch.setattr(llm, "rewrite", lambda q, names: {"language": "ru", "questions": ["2023 yilda yalpi ichki mahsulot hajmi qancha edi?"]})
    r = agent.ask("Какой был ВВП Узбекистана в 2023 году?")
    p = r["parts"][0]
    assert r["engine"] == "gemini+rules" and r["language"] == "ru"
    assert p["status"] == "ok" and p["verification"]["passed"]
    assert any(abs(f.get("value", 0) - 1433975.7) < 0.05 for f in p["facts"])


@needs_db
def test_llm_failure_falls_back_to_rules(agent, monkeypatch):
    from siat_mcp import llm
    monkeypatch.setattr(llm, "rewrite", lambda q, names: None)
    r = agent.ask("2023 yilda yalpi ichki mahsulot hajmi qancha edi?")
    assert r["engine"] == "rules" and r["parts"][0]["status"] == "ok"


def test_llm_disabled_without_key(monkeypatch):
    from siat_mcp import llm
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not llm.enabled() and llm.rewrite("x", []) is None
