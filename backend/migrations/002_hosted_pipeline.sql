-- Feed versions and jobs are workspace-scoped. ZIP bytes stay in private object storage.
CREATE TABLE IF NOT EXISTS workbench.feed_versions (
  workspace_id uuid NOT NULL REFERENCES workbench.workspaces(id),
  id uuid NOT NULL,
  source_name text NOT NULL,
  object_key text NOT NULL,
  sha256 text NOT NULL,
  status text NOT NULL CHECK (status IN ('importing','ready','failed')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS workbench.gtfs_rows (
  workspace_id uuid NOT NULL,
  feed_id uuid NOT NULL,
  file_name text NOT NULL,
  row_number bigint NOT NULL,
  row_data jsonb NOT NULL,
  PRIMARY KEY(workspace_id,feed_id,file_name,row_number),
  FOREIGN KEY(workspace_id,feed_id) REFERENCES workbench.feed_versions(workspace_id,id)
);
CREATE INDEX IF NOT EXISTS gtfs_rows_route ON workbench.gtfs_rows(workspace_id,feed_id,(row_data->>'route_id')) WHERE file_name='routes.txt';
CREATE INDEX IF NOT EXISTS gtfs_rows_trip_route ON workbench.gtfs_rows(workspace_id,feed_id,(row_data->>'route_id')) WHERE file_name='trips.txt';
CREATE INDEX IF NOT EXISTS gtfs_rows_trip_stop_times ON workbench.gtfs_rows(workspace_id,feed_id,(row_data->>'trip_id')) WHERE file_name='stop_times.txt';
CREATE INDEX IF NOT EXISTS gtfs_rows_stop ON workbench.gtfs_rows(workspace_id,feed_id,(row_data->>'stop_id')) WHERE file_name='stops.txt';
CREATE TABLE IF NOT EXISTS workbench.jobs (
  workspace_id uuid NOT NULL REFERENCES workbench.workspaces(id),
  id uuid NOT NULL,
  kind text NOT NULL,
  status text NOT NULL CHECK (status IN ('queued','running','completed','failed')),
  payload jsonb NOT NULL,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,id)
);
CREATE INDEX IF NOT EXISTS jobs_queue ON workbench.jobs(status,created_at) WHERE status='queued';
CREATE TABLE IF NOT EXISTS workbench.job_events (
  workspace_id uuid NOT NULL,
  job_id uuid NOT NULL,
  event_id bigint GENERATED ALWAYS AS IDENTITY,
  message text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,job_id,event_id),
  FOREIGN KEY(workspace_id,job_id) REFERENCES workbench.jobs(workspace_id,id)
);
CREATE INDEX IF NOT EXISTS gtfs_rows_calendar_service ON workbench.gtfs_rows(workspace_id,feed_id,(row_data->>'service_id')) WHERE file_name='calendar.txt';
CREATE INDEX IF NOT EXISTS gtfs_rows_calendar_dates_service ON workbench.gtfs_rows(workspace_id,feed_id,(row_data->>'service_id'),(row_data->>'date')) WHERE file_name='calendar_dates.txt';
