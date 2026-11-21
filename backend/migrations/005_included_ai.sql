CREATE TABLE IF NOT EXISTS transient_gtfs.ai_allowances (
    scope text PRIMARY KEY,
    used integer NOT NULL CHECK (used >= 0),
    expires_at timestamptz
);
GRANT SELECT, INSERT, UPDATE, DELETE ON transient_gtfs.ai_allowances TO gtfs_session_app;
