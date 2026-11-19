CREATE SCHEMA IF NOT EXISTS transient_gtfs;
CREATE TABLE IF NOT EXISTS transient_gtfs.sessions (
    id uuid PRIMARY KEY,
    token_hash bytea NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS transient_expiry ON transient_gtfs.sessions (expires_at);
CREATE TABLE IF NOT EXISTS transient_gtfs.files (
    session_id uuid NOT NULL REFERENCES transient_gtfs.sessions(id) ON DELETE CASCADE,
    name text NOT NULL,
    content bytea NOT NULL,
    size integer NOT NULL,
    PRIMARY KEY (session_id, name)
);
