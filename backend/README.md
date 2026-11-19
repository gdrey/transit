# GTFS Postgres importer

`import_gtfs.py` is an initial, private migration tool. It copies an unchanged GTFS ZIP into Postgres and indexes a subset of the schedule tables by feed version. The ZIP remains the lossless source; the index is not a replacement for all GTFS files or fields. All values in the initial index are text, including GTFS times and stop sequences, so queries must interpret these explicitly. This is not yet a multi-user backend or an export pipeline.

Install the dependencies in a virtual environment, then set `DATABASE_URL` privately and run:

```sh
python import_gtfs.py /path/to/gtfs.zip --source-url https://example.org/gtfs.zip
```

The import runs in one transaction, computes a SHA-256 checksum, and avoids reimporting the same ZIP. Never commit the connection string or put it in a URL argument. Store only a public feed URL in `--source-url`. Schema changes and imports require a direct Postgres connection; application functions should use a pooled connection and appropriately restricted credentials.

The database is currently intended for development only. Before a public hosted workbench, add per-workspace authorization, immutable object storage, version-scoped query APIs, private upload, job orchestration, alert interpretation, edits, output validation, and end-to-end tests. The current script loads one indexed copy globally and is not suitable for exposing user feeds.

## Hosted workspace foundation

`migrations/001_workspaces.sql` and `workspaces.py` are an initial private workspace layer. Workspace access tokens are randomly generated and only their hashes are stored. Feed and output records have workspace-scoped foreign keys, and feed reads must include the workspace ID. A caller must authenticate on every request and validate uploads before calling these functions. This code does not expose any HTTP API and is not connected to the Vercel preview.

The ZIP-in-Postgres implementation is for an isolated development experiment, not a production upload design. Large files should move to private object storage with bounded upload/download and retention. Hosted jobs, authorization, indexing, concurrency control, service-alert handling, change processing, validation, and comparison are still required before exposing this publicly. User AI credentials must stay out of database records and logs.

Run local unit checks with `python -m unittest discover -s backend/tests -v`. `backend/tests/check_neon_isolation.py` exercises the authentication and feed lookup against a migrated **development branch**; pass its connection string on stdin. It rolls back its own test records.
