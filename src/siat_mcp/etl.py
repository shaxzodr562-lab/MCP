"""ETL: siat.stat.uz API -> SQLite.

Usage:
    python -m siat_mcp.etl fetch [--limit N]   # download raw JSON into data/raw (resumable)
    python -m siat_mcp.etl load                # build data/siat.db from data/raw
    python -m siat_mcp.etl all
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from .config import settings

HEADERS = {"User-Agent": "siat-mcp-etl/0.1 (research project)"}
PERIOD_RE = re.compile(r"^(\d{4})(?:-(Q([1-5])|M(\d{2})))?$")


# --------------------------------------------------------------------------- fetch
_throttle_lock = threading.Lock()
_next_slot = 0.0


def _throttle() -> None:
    """Global pacing: at most one request per SIAT_MIN_INTERVAL seconds across all threads."""
    global _next_slot
    with _throttle_lock:
        now = time.monotonic()
        wait = max(0.0, _next_slot - now)
        _next_slot = max(now, _next_slot) + settings.min_interval
    if wait:
        time.sleep(wait)


def _get_json(client: httpx.Client, path: str, retries: int = 8):
    url = f"{settings.base_url}{path}"
    for attempt in range(retries):
        _throttle()
        try:
            r = client.get(url)
            if r.status_code == 429 or r.status_code >= 500:
                ra = r.headers.get("Retry-After")
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else min(60, 2 ** (attempt + 1))
                if attempt == retries - 1:
                    r.raise_for_status()
                time.sleep(delay)
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.TransportError, ValueError):
            if attempt == retries - 1:
                raise
            time.sleep(min(30, 2 ** (attempt + 1)))


def fetch_catalog(client: httpx.Client) -> list[dict]:
    tree = _get_json(client, "/sdmx/json/")
    for node in _walk(tree):  # icons are huge and useless for us
        node.pop("icon_svg", None)
    (settings.raw_dir / "_catalog.json").write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    return tree


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.get("children") or [])


def _leaves(tree):
    return [n for n in _walk(tree) if not n.get("children")]


def fetch_indicator(client: httpx.Client, ind_id: int) -> str:
    out = settings.raw_dir / f"{ind_id}.json"
    if out.exists():
        try:  # a file cut off by a killed run must be re-downloaded
            json.loads(out.read_text(encoding="utf-8"))
            return "cached"
        except ValueError:
            out.unlink()
    table = _get_json(client, f"/sdmx/{ind_id}/table/")
    meta = _get_json(client, f"/sdmx/{ind_id}/metadata/")
    out.write_text(json.dumps({"table": table, "metadata": meta}, ensure_ascii=False), encoding="utf-8")
    return "ok"


def cmd_fetch(limit: int | None) -> None:
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=HEADERS, timeout=settings.timeout) as client:
        tree = fetch_catalog(client)
        ids = [n["id"] for n in _leaves(tree)]
        if limit:
            ids = ids[:limit]
        print(f"catalog: {len(ids)} indicators to fetch", flush=True)
        done = failed = 0
        with ThreadPoolExecutor(max_workers=settings.workers) as pool:
            futs = {pool.submit(fetch_indicator, client, i): i for i in ids}
            for f in as_completed(futs):
                try:
                    f.result()
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    print(f"  FAIL {futs[f]}: {e!r}", file=sys.stderr)
                done += 1
                if done % 200 == 0:
                    print(f"  {done}/{len(ids)} (failed {failed})", flush=True)
        print(f"fetch finished: {done} processed, {failed} failed")


# --------------------------------------------------------------------------- load
ACADEMIC_RE = re.compile(r"^(\d{4})/(\d{4})$")
CUM_MONTHS_RE = re.compile(r"^(\d{4})Q(3|6|9|12)$")  # e.g. 2022Q6 = first 6 months (cumulative)


def canonical_label(label: str) -> str:
    """Fix harmless formatting noise: nbsp/spaces, '*' (preliminary) flag, '2023 Q1', '2022Q6'."""
    s = label.replace("\xa0", " ").strip().rstrip("*").strip()
    s = re.sub(r"^(\d{4})\s+(Q\d)$", r"\1-\2", s)
    m = CUM_MONTHS_RE.match(s)
    if m:
        s = f"{m.group(1)}-Q{int(m.group(2)) // 3}"
    return s


def parse_period(label: str):
    """Return (canonical_label, kind, year, quarter, month, sort_key) or None."""
    label = canonical_label(label)
    m = ACADEMIC_RE.match(label)
    if m:
        y = int(m.group(1))
        return label, "academic_year", y, None, None, y * 100 + 13
    r = _parse_basic(label)
    return (label, *r) if r else None


def _parse_basic(label: str):
    m = PERIOD_RE.match(label)
    if not m:
        return None
    year = int(m.group(1))
    if m.group(3):
        q = int(m.group(3))
        if q == 5:  # source uses YYYY-Q5 for the full-year value inside quarterly series
            return "year_total", year, None, None, year * 100 + 13
        return "quarter", year, q, None, year * 100 + q * 3
    if m.group(4):
        mo = int(m.group(4))
        return "month", year, (mo - 1) // 3 + 1, mo, year * 100 + mo
    return "year", year, None, None, year * 100 + 13


def clean(s):
    """Collapse runs of whitespace (the source has names like 'Farg‘ona  viloyati')."""
    return " ".join(s.split()) if isinstance(s, str) else s


def _meta_get(meta: dict, name_en: str):
    for item in meta.get("meta_data", []):
        if item.get("name_en") == name_en:
            return item
    return None


def cmd_load() -> None:
    cat_path = settings.raw_dir / "_catalog.json"
    if not cat_path.exists():
        sys.exit("No catalog. Run `fetch` first.")
    tree = json.loads(cat_path.read_text(encoding="utf-8"))

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    if settings.db_path.exists():
        settings.db_path.unlink()
    db = sqlite3.connect(settings.db_path)
    db.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))

    # categories + indicator skeletons
    leaves: dict[int, tuple[dict, int | None]] = {}

    def add(node, parent_id, level):
        if node.get("children"):
            db.execute(
                "INSERT INTO categories VALUES (?,?,?,?,?,?,?,?)",
                (node["id"], parent_id, node.get("code"), level, node.get("name_uz"),
                 node.get("name_uzc"), node.get("name_ru"), node.get("name_en")),
            )
            for c in node["children"]:
                add(c, node["id"], level + 1)
        else:
            leaves[node["id"]] = (node, parent_id)

    for top in tree:
        add(top, None, 0)

    member_seen: dict[int, tuple] = {}
    period_ids: dict[str, int] = {}
    skipped_periods: Counter[str] = Counter()
    conflicts: set[int] = set()
    n_obs = n_loaded = n_missing = 0

    for ind_id, (node, cat_id) in leaves.items():
        raw_file = settings.raw_dir / f"{ind_id}.json"
        if not raw_file.exists():
            n_missing += 1
            continue
        raw = json.loads(raw_file.read_text(encoding="utf-8"))
        meta = raw["metadata"]
        unit = _meta_get(meta, "Unit of measurement") or {}
        source = _meta_get(meta, "Primary source of information") or {}
        note = _meta_get(meta, "Note") or {}

        obs_rows, labels = [], []
        for row in raw["table"]:
            rid = row["id"]
            ident = (row.get("code"), row.get("name_uz"))
            if rid not in member_seen:
                member_seen[rid] = ident
                db.execute(
                    "INSERT INTO members VALUES (?,?,?,?,?,?,?,?,?)",
                    (rid, row.get("code"), row.get("parent_id"), int(bool(row.get("is_root"))),
                     int(rid <= 15), clean(row.get("name_uz")), clean(row.get("name_uzc")), clean(row.get("name_ru")),
                     clean(row.get("name_en"))),
                )
            elif member_seen[rid] != ident:
                conflicts.add(rid)
            for cell in row.get("data", []):
                for label, val in cell.items():
                    p = parse_period(label)
                    if p is None:  # source defects (Excel artefacts etc.): skip but record
                        skipped_periods[label] += 1
                        continue
                    canon = p[0]
                    if canon not in period_ids:
                        cur = db.execute(
                            "INSERT INTO periods(label,kind,year,quarter,month,sort_key) VALUES (?,?,?,?,?,?)",
                            p,
                        )
                        period_ids[canon] = cur.lastrowid
                    labels.append((p[5], canon))
                    obs_rows.append((ind_id, rid, period_ids[canon], None if val is None else float(val)))

        labels.sort()
        db.execute(
            "INSERT INTO indicators (id,code,category_id,name_uz,name_uzc,name_ru,name_en,periodicity,"
            "unit_uz,unit_en,source_uz,note_uz,first_period,last_period,n_values,updated_at,metadata_json)"
            " VALUES (" + ",".join("?" * 17) + ")",
            (ind_id, node.get("code"), cat_id, clean(node.get("name_uz")), clean(node.get("name_uzc")),
             clean(node.get("name_ru")), clean(node.get("name_en")), node.get("period"),
             clean(unit.get("value_uz")), clean(unit.get("value_en")), source.get("value_uz"), note.get("value_uz"),
             labels[0][1] if labels else None, labels[-1][1] if labels else None,
             len({(o[1], o[2]) for o in obs_rows}), node.get("updated_xlsx"),
             json.dumps(meta.get("meta_data", []), ensure_ascii=False)),
        )
        db.executemany("INSERT OR REPLACE INTO observations VALUES (?,?,?,?)", obs_rows)
        n_obs += len(obs_rows)
        n_loaded += 1

    db.commit()
    from .classify import classify_all  # local import: classify uses the finished tables

    classify_all(db)
    db.execute("ANALYZE")
    db.close()
    print(f"loaded {n_loaded} indicators, {n_obs} observations, {len(member_seen)} members, "
          f"{len(period_ids)} periods; missing raw files: {n_missing}")
    if conflicts:
        print(f"WARNING member ids with differing code/name across indicators: {sorted(conflicts)[:20]}")
    if skipped_periods:
        out = settings.db_path.parent / "skipped_periods.json"
        out.write_text(json.dumps(skipped_periods, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"skipped {sum(skipped_periods.values())} cells with {len(skipped_periods)} unparseable "
              f"period labels (see {out.name})")


def main() -> None:
    ap = argparse.ArgumentParser(prog="siat_mcp.etl")
    ap.add_argument("cmd", choices=["fetch", "load", "all"])
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.cmd in ("fetch", "all"):
        cmd_fetch(a.limit)
    if a.cmd in ("load", "all"):
        cmd_load()


if __name__ == "__main__":
    main()
