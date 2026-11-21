# Hosted workbench

Vercel serves the FastAPI application at the repository root. The application reuses the GTFS workbench UI and processing engine under `prototype/`. Neon stores each browser session's working files privately. The engine uses disposable scratch space per request. No agency feed is downloaded until an operator imports it.

Pick an agency, enter a public HTTPS source, or upload a ZIP. The first import starts a private temporary session automatically. Review changes before running the pipeline. The download is assembled from the same checked output ZIP shown in the comparison. Sign out with **End session** to remove the active data immediately. Access also expires after two hours. Vercel's daily cleanup and new session creation remove expired records from active storage. Neon manages its own backup and recovery retention separately.

The browser can reveal a temporary bearer credential under **Connect an MCP client**. Configure it as an Authorization header for the Streamable HTTP `/mcp` endpoint. The credential is private and expires with the same session. Clients that require OAuth need a separate pairing flow. The `open_workbench` tool provides a schedule and review panel in MCP Apps hosts. Other clients receive a link to the workbench.

The hosted service uses the restricted credentials in `GTFS_SESSION_DATABASE_URL` and uses `CRON_SECRET` for cleanup. Apply `backend/migrations/004_temp_sessions.sql` before deployment. The app uses a shared capacity limit and limits session storage to 70 MiB. Large or repeated imports can hit this limit. Hosted ZIP and field checks run on the exact output. The independent validator needs a separate Java and validator deployment.

## Included AI pilot

Apply `backend/migrations/005_included_ai.sql` before deploying this mode. On Vercel production, set `GTFS_INCLUDED_AI_PROVIDER=vercel` and configure `GTFS_INCLUDED_AI_MODEL`. The server authenticates to Vercel AI Gateway with the deployment's short-lived OIDC token. A fixed Neon AI Gateway is also supported with server-only `NEON_AI_GATEWAY_TOKEN` and `NEON_AI_GATEWAY_BASE_URL`. Set `GTFS_INCLUDED_AI_ENABLED=1` only after the model and project budget are configured.

The server reserves up to ten model calls per temporary session and fifty total for the pilot, with no automatic reset. The alert batch can run up to five checks. The worker enforces its reservation and limits provider request size and output tokens. Unused reservations are returned after a completed operation. A failed operation can consume its reservation. The pilot counter contains only aggregate usage, and session counters expire with session cleanup. A project-level AI Gateway budget provides a separate spend guard. The gateway can finish a request that crosses the budget before it blocks new requests.

Hosted visitors have no key field. The server sends only alert or edit text and selected schedule context to the configured model. Only submit transit data cleared for external processing. The local prototype retains its optional provider connection.
