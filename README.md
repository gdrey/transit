# Transit GTFS Workbench

An agency-agnostic, local prototype for inspecting a GTFS Schedule, interpreting service alerts, planning supported edits, and comparing an output ZIP with its original. It includes a small MCP server exposing schedule inspection and a separate web workbench for reviewing and drafting changes.

The [hosted workbench](https://transit-workbench.vercel.app/) uses a private, temporary session with no account. Select and import a feed to start working. See [hosted setup and MCP client configuration](hosted/README.md).

Start with the [MCP handoff](docs/MCP_HANDOFF.md) for agreed rules, worked alert examples, tool capabilities, and next implementation steps.

## Run locally

Use Python 3.10 or later and install the prototype dependencies:

```sh
cd prototype
python3 -m pip install -r requirements.txt
python3 preview_server.py
```

Open `http://127.0.0.1:8766/comparison-mockup.html`. Import a static GTFS ZIP from a public HTTPS URL or local file. Connect an OpenAI-compatible `/chat/completions` endpoint, model, and optional API key in **Connect AI service**. The service must support JSON Schema structured responses. A local service can use `http://localhost`; remote endpoints require HTTPS. The page passes connection details to the local server only for the operations that need them. They are held in browser memory and, while an assessment batch runs, server process memory; they are not written to disk. Closing the page clears its inputs; stop the server to end any outstanding batch. Alert text and relevant GTFS context are sent to the configured provider. A provider's own retention policies apply.

The prototype server binds to loopback and is **not** a multi-user hosted service. Do not expose it directly to the internet. A production deployment needs user isolation, authentication, secure secret handling, job storage, and separate hardening.

## MCP server

Start `python3 mcp_server.py` from `prototype` as a stdio MCP server. It exposes `feed_summary`, `search_routes`, `scheduled_trips`, and `check_skipped_stops` for the schedule already loaded through the workbench. It uses the same local feed database. Scheduled data is not live-service confirmation. The MCP server is an initial read-only interface; draft writing, alert intake, and export remain in the prototype and are not MCP tools yet.

Example client configuration (replace the paths):

```json
{"mcpServers":{"transit":{"command":"python3","args":["/absolute/path/to/transit/prototype/mcp_server.py"],"cwd":"/absolute/path/to/transit/prototype"}}}
```

## Scope and validation

The product requirements and modeling decisions are in [docs/PRD.md](docs/PRD.md) and [docs/MODELING_POLICY.md](docs/MODELING_POLICY.md). Supported edits currently include route colors and short names, plus selected skipped-stop, added-stop, and platform changes. Other changes may be assessed or planned without a working writer; the UI identifies unassessed, unresolved, and unsupported work. An incomplete output must not be treated as a complete representation of all imported alerts. Independent MobilityData validation requires separately installed validator tools; it is reported as unavailable when absent.

Local GTFS data, SQLite databases, generated drafts, provider credentials, and dependencies are excluded from this repository.
