import pytest

from siat_mcp import forecast as fc
from siat_mcp.config import settings

needs_db = pytest.mark.skipif(not settings.db_path.exists(), reason="run the ETL first")


def test_linear_series_is_extrapolated_with_interval():
    res = fc.forecast([100 + 5 * i for i in range(15)], 2, 1)
    assert res["ok"]
    (p1, lo1, hi1), (p2, lo2, hi2) = res["points"]
    assert abs(p1 - 175) < 2 and abs(p2 - 180) < 3
    assert lo1 <= p1 <= hi1 and hi2 - lo2 >= hi1 - lo1  # uncertainty never shrinks


def test_seasonal_series_keeps_its_season():
    y = [(100 + t) * (1.5 if t % 4 == 3 else 1.0) for t in range(24)]
    res = fc.forecast(y, 4, 4)
    values = [p for p, _, _ in res["points"]]
    assert res["ok"] and max(values) == values[3] and values[3] > 1.3 * values[0]


def test_short_or_chaotic_series_are_refused():
    assert fc.forecast([1, 2, 3], 1, 1) == {"ok": False, "reason": "too_short"}
    assert fc.forecast([10, 90, 5, 120, 8, 60, 3, 150, 20, 70, 4, 110], 1, 1)["reason"] == "unreliable"


@pytest.fixture(scope="module")
def stats():
    from siat_mcp.queries import Stats
    return Stats()


@needs_db
def test_forecast_only_beyond_the_data_and_within_horizon(stats):
    assert stats.forecast(246, "2020")["reason"] == "not_future"
    assert stats.forecast(246, "2040")["reason"] == "too_far"
    r = stats.forecast(246, "2027")
    assert r["status"] == "ok" and r["low"] < r["value"] < r["high"] and r["value"] > r["last_actual"]["value"]
    assert {f["kind"] for f in r["facts"]} >= {"forecast", "value", "calc"}


@needs_db
def test_cumulative_monthly_forecast_restarts_each_year(stats):
    r = stats.forecast(2401, "2027-M03")  # trade turnover, cumulative within the year
    path = {p["period"]: p["value"] for p in r["path"]}
    assert path["2027-M01"] < path["2027-M02"] < path["2027-M03"] < path["2026-M12"] / 2


@needs_db
def test_agent_labels_forecasts_and_never_presents_them_as_data(stats):
    from siat_mcp.agent import Agent
    agent = Agent(stats)
    p = agent.ask("2027 yilda doimiy aholi soni qancha bo'ladi?")["parts"][0]
    assert p["status"] == "ok" and p["intent"] == "forecast" and p["verification"]["passed"]
    assert "rasmiy ma'lumot emas" in p["answer"]
    past = agent.ask("Samarqand viloyatida 1950 yilda aholi soni qancha edi?")["parts"][0]
    assert past["status"] == "no_data" and not past["cards"]
    real = agent.ask("2023 yilda yalpi ichki mahsulot hajmi qancha edi?")["parts"][0]
    assert real["intent"] == "value"
