# SIAT Statistics MCP

MCP server over official statistics from https://siat.stat.uz (Uzbekistan National Statistics Committee, CC BY 4.0).
Numbers are read and calculated by code, never by the LLM.

## Stack
Python 3.14, SQLite, httpx, official `mcp` SDK.

## Setup (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

## Build the database (stage 2, ETL)
```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python -m siat_mcp.etl fetch   # raw JSON -> data/raw (resumable)
.\.venv\Scripts\python -m siat_mcp.etl load    # data/raw -> data/siat.db
```

## Source API (found in stage 1)
| Endpoint | Content |
|---|---|
| `/api/sdmx/json/` | full catalog tree (5 sectors > 26 > 66 > 3261 indicators) |
| `/api/sdmx/{id}/table/` | values: members x periods |
| `/api/sdmx/{id}/metadata/` | unit, source, methodology, notes |

## Data gotchas
- Period labels: `2023`, `2023-Q2`, `2020-M05`, and `2025-Q5` (= full-year value inside a quarterly series).
- Quarterly/monthly **flow** indicators (volumes in mlrd so'm, output, counts) are cumulative within the year
  (Q4 = annual value). Rates, indices and "%" indicators are not. Of 311 quarterly indicators with full years:
  see `indicators.is_cumulative` (1 cumulative, 0 not, NULL unknown) and `cumulative_basis`; rules in
  `src/siat_mcp/classify.py`, manual decisions in `src/siat_mcp/cumulative_overrides.csv`. Tools must refuse
  period-over-period math on NULL. Note: CPI "relative to previous December" indices are 0 (base-relative, not additive).
- ~0.03% of source cells (367) have unparseable period labels (Excel artefacts like `Unnamed: 4`, `2022.1`);
  they are skipped and listed in `data/skipped_periods.json`. School years `2018/2019` are kind `academic_year`.
- "Region" rows are really dimension members: regions (ids 1..15) or e.g. economic activity A..S.
- Some names mix Cyrillic look-alike letters into Latin words (e.g. "vа bаliq"): normalize in stage 3.
- Units are free text in metadata.

## MCP server (stages 3-5)
```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python -m siat_mcp.server          # stdio
.\.venv\Scripts\python -m siat_mcp.server --http   # streamable HTTP
.\.venv\Scripts\python -m pytest -q                # tests
```
Tools: `search_indicators`, `describe_indicator`, `list_regions`, `get_value`, `get_series`, `calc_change`,
`calc_growth`, `compare_regions`, `top_bottom`. Every result has `source` and `facts`; failures return
`status` = no_data / ambiguous / not_found / refused instead of a guessed number.

Modules: `normalize.py` (text, periods, regions), `search.py` (indicator search), `queries.py`
(exact calculations), `classify.py` (cumulative flags), `server.py` (MCP).

Claude Desktop config (`claude_desktop_config.json`):
```json
{"mcpServers": {"siat": {"command": "C:/Users/Falcon/Desktop/MCP project/.venv/Scripts/python.exe",
  "args": ["-m", "siat_mcp.server"], "env": {"PYTHONPATH": "C:/Users/Falcon/Desktop/MCP project/src"}}}}
```

## Web app (stages 6-8)
```powershell
.\.venv\Scripts\python -m siat_mcp.api                # http://127.0.0.1:8000  (frontend + API)
.\.venv\Scripts\python -m siat_mcp.api --host 0.0.0.0 # reachable from other devices on the network
```
- `web/` - single-page frontend (chat with charts, catalog browser, "how it works"), no build step.
- `POST /api/ask` `{question, indicator_id?, region?, period?}`, `GET /api/health`, `/api/overview`,
  `/api/catalog`, `/api/indicator/{id}`, `/api/series`, docs at `/api/docs`.
- `agent.py`: deterministic question engine (split, region/period/intent detection, indicator search, exact tools,
  Uzbek answer templates) and the stage-7 verifier: every number in an answer must equal a tool fact, otherwise the
  answer is replaced by a refusal. Works without any LLM key (this is also the fallback engine).
- Rate limit: 40 questions/min per client IP.
