import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from siat_mcp.config import settings  # noqa: E402
from siat_mcp.normalize import normalize_text, parse_period_expr  # noqa: E402

needs_db = pytest.mark.skipif(not settings.db_path.exists(), reason="run the ETL first")


def test_normalize_scripts_and_homoglyphs():
    assert normalize_text("Qishloq, o‘rmon vа bаliq xo‘jaligi") == "qishloq ormon va baliq xojaligi"
    assert normalize_text("Ялпи ички маҳсулот") == "yalpi ichki mahsulot"
    assert normalize_text("Тошкент шаҳри") == normalize_text("Toshkent shahri")


@pytest.mark.parametrize("text,label", [
    ("2023", "2023"), ("2023 yil 2-chorak", "2023-Q2"), ("II chorak 2023", "2023-Q2"),
    ("mart 2024", "2024-M03"), ("2023-M03", "2023-M03"), ("март 2024", "2024-M03"),
])
def test_period_expressions(text, label):
    assert parse_period_expr(text)["label"] == label


@pytest.fixture(scope="module")
def stats():
    from siat_mcp.queries import Stats
    return Stats()


@needs_db
def test_exact_value(stats):
    r = stats.get_value(544, "2023")
    assert r["status"] == "ok" and r["value"] == 1433975.7


@needs_db
def test_growth_is_computed_in_code(stats):
    r = stats.calc_growth(544, "2024")
    assert r["from"]["value"] == 1433975.7 and r["to"]["value"] == 1754667.1
    assert r["percent_change"] == round((1754667.1 - 1433975.7) / 1433975.7 * 100, 2)


@needs_db
def test_missing_period_is_refused_not_invented(stats):
    r = stats.get_value(544, "2031")
    assert r["status"] == "no_data" and "value" not in r


@needs_db
def test_ambiguous_region_is_not_guessed(stats):
    r = stats.get_value(1582, "2025-Q2", "Toshkent")
    assert r["status"] == "ambiguous" and len(r["candidates"]) == 2


@needs_db
def test_cumulative_decumulation(stats):
    q1 = stats.get_value(1582, "2025-Q1")["value"]
    q2_cum = stats.get_value(1582, "2025-Q2")["value"]
    q2 = stats.get_value(1582, "2025-Q2", basis="period")["value"]
    assert abs((q2_cum - q1) - q2) < 1e-6


@needs_db
def test_unknown_cumulative_refuses_period_growth(stats):
    assert stats.calc_growth(3426, "2025-Q2", versus="previous_period")["status"] == "refused"


@needs_db
def test_search_rejects_out_of_scope(stats):
    assert stats.search_indicators("bitcoin narxi")["status"] == "no_match"
    assert stats.search_indicators("yalpi ichki mahsulot yillik")["results"][0]["periodicity"] == "Yillik"
