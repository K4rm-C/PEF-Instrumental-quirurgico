-- PEF MonitoringService (etapa 11): bounded history of technical health checks.
-- Idempotent and data-preserving: CREATE ... IF NOT EXISTS only; safe to run on every start-up.
-- Nothing here references or changes operational data (sessions, counts, users...).
--
-- monitoring_run   : one row per collector cycle (all components checked together).
-- monitoring_check : one row per component per cycle. Never stores secrets, tokens or full
--                    connection strings: `endpoint` is scheme://host:port/path without credentials.
-- Retention: the collector deletes rows older than MONITORING_RETENTION_DAYS (default 7), hourly.

CREATE TABLE IF NOT EXISTS monitoring_run (
    id            uuid PRIMARY KEY,
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz NOT NULL,
    components    smallint    NOT NULL,
    failures      smallint    NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_monitoring_run_started ON monitoring_run (started_at DESC);

CREATE TABLE IF NOT EXISTS monitoring_check (
    id            uuid PRIMARY KEY,
    run_id        uuid        NOT NULL REFERENCES monitoring_run (id) ON DELETE CASCADE,
    component     varchar(40) NOT NULL,
    checked_at    timestamptz NOT NULL,
    status        varchar(10) NOT NULL,
    latency_ms    numeric(10, 2),
    http_status   smallint,
    dependencies  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error_type    varchar(40),
    endpoint      varchar(200),
    details       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT ck_monitoring_check_status CHECK (status IN ('up', 'degraded', 'down', 'unknown')),
    CONSTRAINT ck_monitoring_check_latency CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_monitoring_check_component_time ON monitoring_check (component, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_monitoring_check_time ON monitoring_check (checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_monitoring_check_run ON monitoring_check (run_id);
