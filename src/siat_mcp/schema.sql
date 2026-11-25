PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Catalog tree levels 0..2 (sector, subsector, section)
CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY,
    parent_id  INTEGER REFERENCES categories(id),
    code       TEXT,
    level      INTEGER NOT NULL,
    name_uz    TEXT,
    name_uzc   TEXT,
    name_ru    TEXT,
    name_en    TEXT
);

-- Indicator catalog (tree level 3)
CREATE TABLE IF NOT EXISTS indicators (
    id           INTEGER PRIMARY KEY,
    code         TEXT UNIQUE,
    category_id  INTEGER REFERENCES categories(id),
    name_uz      TEXT,
    name_uzc     TEXT,
    name_ru      TEXT,
    name_en      TEXT,
    periodicity  TEXT,            -- Yillik | Choraklik | Oylik | Yangilanmaydigan
    unit_uz      TEXT,            -- "Unit of measurement" from metadata
    unit_en      TEXT,
    source_uz    TEXT,
    note_uz      TEXT,
    first_period TEXT,
    last_period  TEXT,
    n_values     INTEGER DEFAULT 0,
    updated_at   TEXT,
    metadata_json TEXT,
    -- Sub-annual series semantics (see classify.py): 1 = cumulative within the year (Q4 = annual),
    -- 0 = not cumulative (rate/index/level), NULL = unknown/needs review or not applicable (annual)
    is_cumulative   INTEGER,
    cumulative_basis TEXT,
    within_score    REAL,   -- share of within-year steps that do not decrease
    reset_score     REAL    -- share of year boundaries where the series drops back (cumulative reset)
);

-- Dimension members as served in each indicator's table: geographic units
-- (is_region=1, ids 1..15) or other classifier members (economic activity A..S, etc.)
CREATE TABLE IF NOT EXISTS members (
    id         INTEGER PRIMARY KEY,
    code       TEXT,
    parent_id  INTEGER,
    is_root    INTEGER DEFAULT 0,
    is_region  INTEGER DEFAULT 0,
    name_uz    TEXT,
    name_uzc   TEXT,
    name_ru    TEXT,
    name_en    TEXT
);

-- Time periods in canonical form: 2023 | 2023-Q2 | 2023-M05
CREATE TABLE IF NOT EXISTS periods (
    id      INTEGER PRIMARY KEY,
    label   TEXT UNIQUE NOT NULL,
    kind    TEXT NOT NULL,        -- year | quarter | month | year_total (label YYYY-Q5)
    year    INTEGER,
    quarter INTEGER,
    month   INTEGER,
    sort_key INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    indicator_id INTEGER NOT NULL REFERENCES indicators(id),
    member_id    INTEGER NOT NULL REFERENCES members(id),
    period_id    INTEGER NOT NULL REFERENCES periods(id),
    value        REAL,            -- NULL = missing in source
    PRIMARY KEY (indicator_id, member_id, period_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_obs_member_period ON observations(member_id, period_id);
CREATE INDEX IF NOT EXISTS idx_obs_period ON observations(period_id);
CREATE INDEX IF NOT EXISTS idx_ind_category ON indicators(category_id);
CREATE INDEX IF NOT EXISTS idx_ind_periodicity ON indicators(periodicity);
CREATE INDEX IF NOT EXISTS idx_periods_year ON periods(year);
