"""MCP server for official Uzbekistan statistics (siat.stat.uz).

Run (stdio):   python -m siat_mcp.server
Run (HTTP):    python -m siat_mcp.server --http
"""
from __future__ import annotations

import argparse
from typing import Literal

from mcp.server.mcpserver import MCPServer

from .queries import Stats

INSTRUCTIONS = """\
You answer questions about official statistics of Uzbekistan using ONLY these tools.
Rules:
1. Never state a number that did not come from a tool result (see each result's `facts`). Never compute
   percentages, differences, sums or rankings yourself: use calc_growth / calc_change / compare_regions /
   top_bottom.
2. Workflow: search_indicators -> (describe_indicator if unit/period unclear) -> get_value / get_series /
   calc_* . Pick the indicator whose name, periodicity and unit match the question; if `confident` is false
   or several candidates differ in meaning (current vs constant prices, annual vs quarterly), say which
   one you used or ask the user.
3. Always mention the period, region and unit of every number, and name the source (siat.stat.uz).
4. status != "ok": do not guess. no_data -> say the data is not available and quote available_periods.
   ambiguous -> ask the user to choose among the candidates. refused/not_found/no_match -> explain plainly.
5. Never guess future values yourself. For a period after the last published one, call `forecast` and present
   the result clearly as an ESTIMATE (not official data), with its interval and backtest error. If it is
   refused, say so. Decline questions about data outside the catalog.
6. Quarterly/monthly flow series are often cumulative within the year; tools handle this (see notes).
"""

mcp = MCPServer("siat-statistics", instructions=INSTRUCTIONS)
_stats: Stats | None = None


def stats() -> Stats:
    global _stats
    if _stats is None:
        _stats = Stats()
    return _stats


Basis = Literal["reported", "period"]


@mcp.tool()
def search_indicators(query: str, limit: int = 8, periodicity: Literal["Yillik", "Choraklik", "Oylik"] | None = None) -> dict:
    """Find statistical indicators in the SIAT catalog (3261 indicators) by a natural-language query in
    Uzbek (Latin or Cyrillic) or English, e.g. "yalpi ichki mahsulot", "o'rtacha oylik ish haqi".
    Returns ranked candidates with id, name, unit, periodicity and available period range.
    Optionally filter by periodicity (Yillik=annual, Choraklik=quarterly, Oylik=monthly)."""
    return stats().search_indicators(query, limit, periodicity)


@mcp.tool()
def describe_indicator(indicator_id: int) -> dict:
    """Full description of one indicator: unit, periodicity, period range, which regions/members it has
    data for, methodology, notes and caveats (e.g. cumulative quarterly values)."""
    return stats().describe_indicator(indicator_id)


@mcp.tool()
def list_regions() -> dict:
    """List the regions (Republic, Karakalpakstan, Tashkent city, provinces) with their ids."""
    return stats().list_regions()


@mcp.tool()
def get_value(indicator_id: int, period: str, region: str | None = None, basis: Basis = "reported") -> dict:
    """Exact value of an indicator for one period and region/member.
    period: '2023', '2023-Q2', '2024-M03' or text like '2023 yil 2-chorak', 'mart 2024'.
    region: name (Latin/Cyrillic, e.g. 'Samarqand', 'Тошкент шаҳри'), id, or omitted for the whole Republic.
    basis: 'reported' = as published; 'period' = single quarter/month value for cumulative series."""
    return stats().get_value(indicator_id, period, region, basis)


@mcp.tool()
def get_series(indicator_id: int, region: str | None = None, start: str | None = None, end: str | None = None,
               basis: Basis = "reported") -> dict:
    """Time series (dynamics) of an indicator for one region/member between optional start and end periods."""
    return stats().get_series(indicator_id, region, start, end, basis)


@mcp.tool()
def calc_change(indicator_id: int, period_from: str, period_to: str, region: str | None = None,
                basis: Basis = "reported") -> dict:
    """Absolute and percentage change between two explicit periods, computed exactly in code."""
    return stats().calc_change(indicator_id, period_from, period_to, region, basis)


@mcp.tool()
def calc_growth(indicator_id: int, period: str, region: str | None = None,
                versus: Literal["previous_year", "previous_period"] = "previous_year") -> dict:
    """Growth/decline (%) of `period` versus the same period of the previous year ('previous_year') or versus
    the immediately preceding period ('previous_period'). Handles cumulative quarterly/monthly series."""
    return stats().calc_growth(indicator_id, period, region, versus)


@mcp.tool()
def compare_regions(indicator_id: int, regions: list[str], period: str, basis: Basis = "reported") -> dict:
    """Compare two or more regions on one indicator and period: ranking, highest/lowest, difference, ratio."""
    return stats().compare_regions(indicator_id, regions, period, basis)


@mcp.tool()
def top_bottom(indicator_id: int, period: str, order: Literal["highest", "lowest"] = "highest", limit: int = 5,
               basis: Basis = "reported") -> dict:
    """Regions with the highest or lowest value of an indicator in a period (Republic total excluded)."""
    return stats().top_bottom(indicator_id, period, order, limit, True, basis)


@mcp.tool()
def forecast(indicator_id: int, period: str | None = None, region: str | None = None) -> dict:
    """ESTIMATE a value for a period after the last published one (annual, quarterly or monthly), computed in code
    by a backtested time-series model. Returns point, uncertainty interval (covered ~70-80% of cases in backtests), model and past error. Not official data.
    period omitted = next period. Refused when too far ahead, series too short, or past errors too large."""
    return stats().forecast(indicator_id, period, region)


def main() -> None:
    ap = argparse.ArgumentParser(prog="siat_mcp.server")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    a = ap.parse_args()
    mcp.run("streamable-http" if a.http else "stdio")


if __name__ == "__main__":
    main()
