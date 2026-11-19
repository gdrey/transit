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

## Hosted pipeline work in progress

The development branch now has private object storage and `002_hosted_pipeline.sql`. `cloud_import.py` indexes a selected GTFS archive into workspace- and version-scoped Postgres rows; `cloud_schedule.py` reads routes, trips and stop times; `cloud_jobs.py` provides job records; `cloud_route_edits.py` builds and verifies a route metadata output ZIP. These pieces are not wired into an HTTP API, background worker, or the Vercel UI yet. They do not implement service-alert edits or a full hosted workbench. The bucket and schema are on the development branch only.

## Hosted MCP development

`hosted_mcp.py` is a development ASGI implementation using the MCP SDK's Streamable HTTP transport and Neon OAuth token verification. It exposes only `list_feeds`, `list_routes`, `scheduled_trips`, and `trip_stop_times`; it does not import or edit feeds. Requests require a valid token with the configured issuer and audience. A signed-in subject must belong to exactly one workspace. It is not deployed or connected to the workbench.

Neon Auth is enabled on the isolated development branch. Completing the remote service requires hosted login and consent pages, OAuth server configuration and verification with real clients, a user-to-workspace provisioning flow, and Vercel/API deployment. Do not expose it until token verification, workspace isolation, rate limits, and end-to-end behavior are independently verified. An MCP App UI and write operations remain to be built.
