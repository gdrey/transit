# Hosted architecture proposal

The deployed `hosted/` site is a browser-only, read-only GTFS explorer. It is not the editing workbench. The full prototype currently uses a single local SQLite database and local files; it cannot safely serve multiple hosted users unchanged.

## Recommended architecture

1. Vercel serves the web interface and authenticated API entry points. A background worker or durable workflow handles downloads, ZIP validation, feed indexing, alert assessment, draft creation, and canonical validation. Jobs must have timeouts, retries, idempotency keys, logs without secrets, and persisted state.
2. Object storage keeps original and generated ZIPs and validation reports as immutable, access-controlled objects. Store checksums and metadata in the database. For example, use Neon Object Storage when available in the selected region, or a compatible object store.
3. Neon-hosted Postgres indexes the schedule and stores workspaces, versioned sources, alerts, assessments, modeling plans, edit decisions, job events, and output manifests. Scope every query by workspace and feed version. The original ZIP remains the authoritative source for preserving optional and unknown files.
4. The AI adapter accepts a user-selected provider and model. The user supplies any credential per session; do not commit it, put it in URLs, write it to the database, or log it. An in-flight job must either receive its credential in transient memory with a bounded lifetime or pause and request it again after a restart. Do not promise durable retries without designing credential lifetime explicitly.
5. The MCP server and UI call the same authenticated service layer and deterministic GTFS operations. MCP clients must not receive direct database credentials.

## Suggested data model

- `workspace` and access-control membership.
- `feed_version`: workspace, source, imported time, timezone, content hash, object key, validation and ingestion status.
- Feed-scoped `agencies`, `routes`, `stops`, `trips`, `stop_times`, `calendar`, `calendar_dates`, `shapes`, `frequencies`, and relevant optional tables. Use composite keys including `feed_version_id`; preserve raw columns and original ZIP for fields not yet understood.
- `alert_snapshot` and `alert`: source identifiers, retrieved time, raw content, active periods, normalized interpretation, and independently assigned change ID.
- `assessment`: change ID, exact feed version, structured evidence, result, interpretation version and clarification history.
- `change_request`, `change_operation`, `job`, `job_event`, and `output_version`: status, plan, provenance, affected records, hashes, validation report, exclusions and reasons.

Index by `(feed_version_id, route_id, direction_id)`, `(feed_version_id, trip_id, stop_sequence)`, service and date, and affected stop/time based on measured workloads. Store GTFS times as integer seconds from service-day start, including values over 24:00; keep the agency timezone and implement GTFS service-day semantics when converting to instants. Weekly calendar rules and calendar exceptions both affect active service; frequency trips need explicit expansion or specialized query logic.

## Versioning and correctness

Do not create a physical database branch per alert or feed. Use application-level feed-version IDs and immutable source/output objects for routine history. Database branches are useful for isolated development, testing and migration previews. Build a new output from the immutable base and selected changes; compare and independently validate the exact object offered for download. Checks should distinguish reflected, not reflected and insufficient evidence. Multi-alert conflicts and unsupported dependent files must remain visible and block only the affected work according to the product policy.

## Migration phases

1. Inventory capabilities and create contract tests for import, schedule queries and export verification.
2. Provision an isolated development database and private object storage; define tenancy, retention and schema migrations.
3. Bulk import one public feed into Postgres; compare route counts, service dates, after-midnight times and sample trip/stop sequences against the existing prototype.
4. Port deterministic queries and checks behind a provider-independent API. Add job state and progress reporting; retain local execution during migration.
5. Port changes and ZIP generation, verifying unknown-file preservation and every changed record against the generated object. Add the canonical GTFS validator and compare notices.
6. Connect the hosted UI and remote MCP to the same API, then test multiple users, failures, key expiry, large feeds and concurrent jobs before inviting external users.

No database, object store, worker, or remote MCP has been provisioned by this proposal.

## References

- [Neon backend overview](https://neon.com/docs/get-started/backend-overview)
- [Neon Object Storage](https://neon.com/docs/storage/overview)
- [Neon branching](https://neon.com/docs/introduction/branching)
- [GTFS Schedule reference](https://gtfs.org/documentation/schedule/reference/)

## Initial development import

An initial private Neon development database has a single public SEPTA bus feed. `backend/import_gtfs.py` preserves its full ZIP and imports an index of the core schedule tables in one transaction, keyed by feed version and ZIP SHA-256. This first importer is a migration experiment: it does not implement workspace isolation, authentication, file storage, service-edit operations, output comparison, or a hosted API. GTFS values are stored as text in the index and need explicit parsing for numeric stop order and times past midnight.

The read-only Vercel preview is not connected to the database. Do not connect a public deployment to this initial schema until workspace authorization and appropriate restricted database roles are implemented.
