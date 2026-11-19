# Hosted workbench

Vercel serves the FastAPI application at the repository root. The application reuses the GTFS workbench UI and processing engine under `prototype/`. Neon stores each browser session's working files privately. The engine uses disposable scratch space per request. No agency feed is downloaded until an operator imports it.

Click **Start temporary session**, then pick an agency, enter a public HTTPS source, or upload a ZIP. Review changes before running the pipeline. The download is assembled from the same checked output ZIP shown in the comparison. Sign out with **End session** to remove the active data immediately. Access also expires after two hours. Vercel's daily cleanup and new session creation remove expired records from active storage. Neon manages its own backup and recovery retention separately.

The browser can reveal a temporary bearer credential under **Connect an MCP client**. Configure it as an Authorization header for the Streamable HTTP `/mcp` endpoint. The credential is private and expires with the same session. Clients that require OAuth and embedded MCP Apps need further integration. The website remains usable independently.

The hosted service uses the restricted credentials in `GTFS_SESSION_DATABASE_URL` and uses `CRON_SECRET` for cleanup. Apply `backend/migrations/004_temp_sessions.sql` before deployment. The app uses a shared capacity limit and limits session storage to 70 MiB. Large or repeated imports can hit this limit. Hosted ZIP and field checks run on the exact output. The independent validator needs a separate Java and validator deployment.
