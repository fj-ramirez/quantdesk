-- Canonical schema (spec 1.1).
--
-- Portability note: this is deliberately plain SQL. The engine is DuckDB today
-- (single-user analytical workload, file-based) but the DDL is kept close to
-- ANSI so the same tables can be created in Postgres later without a redesign.
-- Anything DuckDB-specific belongs in db.py, not here.

CREATE TABLE IF NOT EXISTS series_metadata (
    series_id           TEXT PRIMARY KEY,
    display_name        TEXT    NOT NULL,
    source              TEXT    NOT NULL,
    source_code         TEXT    NOT NULL,
    asset_class         TEXT    NOT NULL,
    category            TEXT    NOT NULL,
    unit                TEXT    NOT NULL,
    frequency           TEXT    NOT NULL,
    default_transform   TEXT    NOT NULL,
    revisable           BOOLEAN NOT NULL,
    -- 'source_vintage' | 'ingest_time'. See models.VINTAGE_SOURCES: it records
    -- whether as_of is a real vintage from the publisher or our first-seen time.
    vintage_source      TEXT    NOT NULL,
    -- Spec 7. NULL means the series does not honour the global convention
    -- (e.g. 24-hour FX); that is recorded, not assumed away.
    snapshot_tz         TEXT,
    snapshot_local_time TEXT,
    notes               TEXT
);

-- The point-in-time table. The as_of column in the primary key is the whole
-- design: one (series, date) may hold many vintages and none overwrites another.
CREATE TABLE IF NOT EXISTS observations (
    series_id    TEXT        NOT NULL,
    value_date   DATE        NOT NULL,
    as_of        TIMESTAMPTZ NOT NULL,
    value        DOUBLE      NOT NULL,
    -- How as_of was established for THIS row: source_vintage | derived_lag |
    -- archive_floor. See models.AS_OF_BASES. A series mixes these, so the
    -- provenance belongs on the observation, not only on the metadata row.
    as_of_basis  TEXT        NOT NULL,
    source_batch TEXT        NOT NULL,
    PRIMARY KEY (series_id, value_date, as_of)
);

CREATE INDEX IF NOT EXISTS observations_series_asof
    ON observations (series_id, as_of);

CREATE TABLE IF NOT EXISTS releases (
    release_id      TEXT PRIMARY KEY,
    series_id       TEXT        NOT NULL,
    scheduled_at    TIMESTAMPTZ NOT NULL,
    consensus       DOUBLE,
    -- Spec 2.3: must be strictly before scheduled_at. Enforced by the loader
    -- when this table starts being written (Phase 4), not by a CHECK, so the
    -- violation can be reported with the offending release_id.
    consensus_as_of TIMESTAMPTZ,
    prior           DOUBLE,
    actual          DOUBLE,
    actual_as_of    TIMESTAMPTZ
);

-- Every ingestion run, so any stored value can be traced back to the fetch that
-- produced it (spec 0.4).
CREATE TABLE IF NOT EXISTS ingest_batches (
    source_batch TEXT PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ,
    adapter      TEXT,
    args         TEXT,
    status       TEXT NOT NULL,
    note         TEXT
);

-- Transmission graph (spec 4). Split into a definition table and a stats table
-- rather than the spec's single `edges` table, for the same reason observations
-- carry an as_of: the empirical half is recomputed nightly and overwriting it
-- would destroy the history of how a relationship changed, which is precisely
-- the signal spec 4 says matters most.
CREATE TABLE IF NOT EXISTS edge_definitions (
    from_series      TEXT    NOT NULL,
    to_series        TEXT    NOT NULL,
    -- +1 / -1 / 0, the prior from theory. ZERO IS A REAL VALUE, not a missing
    -- one: it means the sign is genuinely regime-dependent and asserting one
    -- would mislead exactly when it matters (spec 4, equity/rates).
    expected_sign    INTEGER NOT NULL,
    typical_lag_days INTEGER NOT NULL,
    chain            TEXT,
    note             TEXT,
    PRIMARY KEY (from_series, to_series)
);

CREATE TABLE IF NOT EXISTS edge_stats (
    from_series     TEXT        NOT NULL,
    to_series       TEXT        NOT NULL,
    as_of           TIMESTAMPTZ NOT NULL,
    value_date      DATE        NOT NULL,
    beta            DOUBLE,
    beta_window     INTEGER     NOT NULL,
    beta_t_stat     DOUBLE,
    r_squared       DOUBLE,
    corr            DOUBLE,
    -- Where this correlation sits in its own trailing history. Spec 4 calls
    -- this and sign_conflict the two highest-value outputs here.
    corr_percentile DOUBLE,
    corr_history_n  INTEGER,
    sign_conflict   BOOLEAN,
    significant     BOOLEAN,
    n_obs           INTEGER     NOT NULL,
    source_batch    TEXT        NOT NULL,
    PRIMARY KEY (from_series, to_series, as_of)
);
