-- Subject is the verified OAuth token's sub, not a client-supplied field.
CREATE TABLE IF NOT EXISTS workbench.workspace_members (
  workspace_id uuid NOT NULL REFERENCES workbench.workspaces(id),
  subject text NOT NULL,
  role text NOT NULL CHECK (role IN ('owner','editor','viewer')),
  PRIMARY KEY(workspace_id, subject)
);
CREATE INDEX IF NOT EXISTS workspace_members_subject ON workbench.workspace_members(subject,workspace_id);
