from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional at runtime
    pass

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    base_url: str = os.getenv("SIAT_BASE_URL", "https://siat.stat.uz/api").rstrip("/")
    db_path: Path = ROOT / os.getenv("SIAT_DB_PATH", "data/siat.db")
    raw_dir: Path = ROOT / os.getenv("SIAT_RAW_DIR", "data/raw")
    workers: int = int(os.getenv("SIAT_ETL_WORKERS", "3"))
    min_interval: float = float(os.getenv("SIAT_MIN_INTERVAL", "0.4"))  # seconds between requests
    timeout: float = float(os.getenv("SIAT_HTTP_TIMEOUT", "60"))


settings = Settings()
