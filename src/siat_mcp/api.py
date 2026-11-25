"""Stage 8: HTTP API + static frontend.

Run:  python -m siat_mcp.api [--host 127.0.0.1] [--port 8000]
"""
from __future__ import annotations

import argparse
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import llm
from .agent import Agent
from .config import ROOT, settings
from .queries import Stats

WEB_DIR = ROOT / "web"
RATE_LIMIT = 40  # requests per minute per client for /api/ask
_hits: dict[str, deque] = defaultdict(deque)

app = FastAPI(title="SIAT statistics assistant", version="0.1.0", docs_url="/api/docs", redoc_url=None)
_stats: Stats | None = None
_agent: Agent | None = None
_cat: dict | None = None


def stats() -> Stats:
    global _stats, _agent
    if _stats is None:
        _stats = Stats()
        _agent = Agent(_stats)
    return _stats


def agent() -> Agent:
    stats()
    return _agent  # type: ignore[return-value]


class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=600)
    indicator_id: int | None = None
    region: str | None = Field(None, max_length=80)
    period: str | None = Field(None, max_length=30)


def _client_ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/health")
def health():
    s = stats()
    n_ind = s.db.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
    return {"status": "ok", "indicators": n_ind, "engine": "gemini+rules" if llm.enabled() else "rules", "llm": llm.status(), "db": Path(settings.db_path).name,
            "time": int(time.time())}


@app.get("/api/overview")
def overview():
    s = stats()
    q = lambda sql: s.db.execute(sql).fetchone()[0]  # noqa: E731
    sectors = s.db.execute("SELECT id, name_uz FROM categories WHERE level=0 ORDER BY id").fetchall()
    counts = _category_counts()
    return {
        "indicators": q("SELECT COUNT(*) FROM indicators"),
        "observations": q("SELECT COUNT(*) FROM observations"),
        "regions": q("SELECT COUNT(*) FROM members WHERE is_region=1"),
        "periods": q("SELECT COUNT(*) FROM periods"),
        "first_year": q("SELECT MIN(year) FROM periods WHERE year IS NOT NULL"),
        "last_year": q("SELECT MAX(year) FROM periods WHERE year IS NOT NULL"),
        "updated": q("SELECT MAX(updated_at) FROM indicators"),
        "sectors": [{"id": r["id"], "name": r["name_uz"], "count": counts.get(r["id"], 0)} for r in sectors],
        "examples": EXAMPLES,
    }


EXAMPLES = [
    {"icon": "📈", "text": "2024 yilda O'zbekiston YaIM necha foiz o'sdi?"},
    {"icon": "🏙️", "text": "Samarqand va Buxoroning 2024 yil yalpi hududiy mahsulotini solishtir"},
    {"icon": "💰", "text": "Qaysi viloyatda o'rtacha oylik ish haqi eng yuqori?"},
    {"icon": "👥", "text": "Doimiy aholi soni dinamikasi"},
    {"icon": "🚢", "text": "2020 dan 2024 gacha eksport hajmi qancha o'zgargan?"},
    {"icon": "🧪", "text": "2031 yilda ishsizlik qancha bo'ladi?"},
]


@app.post("/api/ask")
def ask(body: AskBody, request: Request):
    ip, now = _client_ip(request), time.time()
    dq = _hits[ip]
    while dq and now - dq[0] > 60:
        dq.popleft()
    if len(dq) >= RATE_LIMIT:
        return JSONResponse({"detail": "Juda ko'p so'rov. Bir daqiqadan keyin urinib ko'ring."}, status_code=429)
    dq.append(now)
    return agent().ask(body.question, indicator_id=body.indicator_id, region=body.region, period=body.period)


# ---------------------------------------------------------------- catalog
def _category_counts() -> dict[int, int]:
    global _cat
    if _cat is None:
        s = stats()
        parent = {r["id"]: r["parent_id"] for r in s.db.execute("SELECT id, parent_id FROM categories")}
        counts: dict[int, int] = defaultdict(int)
        for r in s.db.execute("SELECT category_id FROM indicators"):
            c = r["category_id"]
            while c is not None:
                counts[c] += 1
                c = parent.get(c)
        _cat = dict(counts)
    return _cat


@app.get("/api/categories")
def categories():
    s = stats()
    counts = _category_counts()
    rows = s.db.execute("SELECT id, parent_id, level, name_uz FROM categories ORDER BY level, id").fetchall()
    return [{"id": r["id"], "parent_id": r["parent_id"], "level": r["level"], "name": r["name_uz"],
             "count": counts.get(r["id"], 0)} for r in rows]


@app.get("/api/catalog")
def catalog(q: str | None = None, category: int | None = None, periodicity: str | None = None,
            limit: int = Query(40, ge=1, le=100), offset: int = Query(0, ge=0)):
    s = stats()
    if q and q.strip():
        hits = s.catalog.search(q, limit=100, periodicity=periodicity)
        items = [h.as_dict() for h in hits]
        if category:
            allowed = _descendant_indicators(category)
            items = [i for i in items if i["indicator_id"] in allowed]
        return {"total": len(items), "items": items[offset:offset + limit], "mode": "search"}
    where, args = [], []
    if category:
        ids = _descendant_categories(category)
        where.append(f"category_id IN ({','.join('?' * len(ids))})")
        args += ids
    if periodicity:
        where.append("periodicity = ?")
        args.append(periodicity)
    cond = ("WHERE " + " AND ".join(where)) if where else ""
    total = s.db.execute(f"SELECT COUNT(*) FROM indicators {cond}", args).fetchone()[0]
    rows = s.db.execute(
        f"SELECT id, code, name_uz, periodicity, unit_uz, first_period, last_period, is_cumulative FROM indicators "
        f"{cond} ORDER BY code LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    return {"total": total, "mode": "browse", "items": [
        {"indicator_id": r["id"], "code": r["code"], "name": r["name_uz"], "periodicity": r["periodicity"],
         "unit": r["unit_uz"], "period_range": [r["first_period"], r["last_period"]],
         "is_cumulative": r["is_cumulative"]} for r in rows]}


def _descendant_categories(cid: int) -> list[int]:
    s = stats()
    kids = defaultdict(list)
    for r in s.db.execute("SELECT id, parent_id FROM categories"):
        kids[r["parent_id"]].append(r["id"])
    out, stack = [], [cid]
    while stack:
        c = stack.pop()
        out.append(c)
        stack += kids.get(c, [])
    return out


def _descendant_indicators(cid: int) -> set[int]:
    ids = _descendant_categories(cid)
    rows = stats().db.execute(
        f"SELECT id FROM indicators WHERE category_id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return {r["id"] for r in rows}


@app.get("/api/indicator/{indicator_id}")
def indicator(indicator_id: int, region: str | None = None, basis: str = "reported"):
    s = stats()
    info = s.describe_indicator(indicator_id)
    if info["status"] != "ok":
        raise HTTPException(404, info.get("message", "not found"))
    ser = s.get_series(indicator_id, region, None, None, "period" if basis == "period" else "reported")
    all_members = s.indicator_members(indicator_id)
    info["members"] = [{"id": m["id"], "name": m["name_uz"], "is_region": bool(m["is_region"])} for m in all_members]
    info["caveats"] = []
    if info["periodicity"] in ("Choraklik", "Oylik"):
        if info["is_cumulative"] == 1:
            info["caveats"].append("Qiymatlar yil boshidan jamlanma ko'rinishida (4-chorak/12-oy = butun yil). "
                                   "Yagona chorak yoki oy qiymatini ko'rish uchun «Choraklik/oylik»ni tanlang.")
        elif info["is_cumulative"] is None:
            info["caveats"].append("Bu ko'rsatkichning qiymatlari jamlanma ekani hali aniqlanmagan: "
                                   "davrlararo taqqoslash rad etiladi.")
    info["series"] = ser if ser["status"] == "ok" else None
    info["series_error"] = None if ser["status"] == "ok" else ser
    return info


@app.get("/api/series")
def series(indicator_id: int, region: str | None = None, start: str | None = None, end: str | None = None,
           basis: str = "reported"):
    return stats().get_series(indicator_id, region, start, end, basis)


# ---------------------------------------------------------------- static frontend
if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (WEB_DIR / path).resolve()
        if path and f.is_file() and WEB_DIR in f.parents:
            return FileResponse(f)
        return FileResponse(WEB_DIR / "index.html")


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(prog="siat_mcp.api")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
