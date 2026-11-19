-- Separate namespace: does not expose the earlier development feed.
CREATE SCHEMA IF NOT EXISTS workbench;
CREATE TABLE IF NOT EXISTS workbench.workspaces (
    id uuid PRIMARY KEY,
    token_hash bytea NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS workbench.feeds (
    id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workbench.workspaces(id),
    sha256 text NOT NULL,
    source_name text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    original_zip bytea NOT NULL,
    UNIQUE(workspace_id, id)
);
CREATE INDEX IF NOT EXISTS feeds_workspace_idx ON workbench.feeds(workspace_id, imported_at DESC);
CREATE TABLE IF NOT EXISTS workbench.operations (
    id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL,
    feed_id uuid NOT NULL,
    operation jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(workspace_id, feed_id) REFERENCES workbench.feeds(workspace_id, id)
);
CREATE TABLE IF NOT EXISTS workbench.outputs (
    id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL,
    feed_id uuid NOT NULL,
    sha256 text NOT NULL,
    archive bytea NOT NULL,
    manifest jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(workspace_id, feed_id) REFERENCES workbench.feeds(workspace_id, id)
);
