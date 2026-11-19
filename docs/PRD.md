# Transit GTFS Workbench: product requirements

## Goal

Help a transit data editor combine a GTFS Schedule with service-change notices and feed-edit requests, determine whether each change is reflected, produce a traceable draft where supported, and compare and download the exact output feed.

## Users and design principles

Transit schedule editors and analysts work with different agencies and source formats. The workflow must be agency-agnostic, transparent, compact, and reviewable. GTFS data and deterministic validation belong in the core; language-model providers are replaceable. The LLM interprets untrusted text into a bounded structured proposal, not arbitrary executable code. It must not invent route IDs or claim a change is present without evidence.

## Workflow

1. Configure a user-supplied AI service for the session; import a static feed from a URL or ZIP. Retain an immutable original and hash the feed.
2. Explore routes, direction, service dates, stop patterns, time–stop diagrams and available shapes.
3. Enter feed edits in natural language and import alerts from paste, webpage, GTFS-Realtime, or source-specific adapters. Preserve source text, retrieval time, active periods and a workbench change ID.
4. Assess each against the loaded feed: reflected, not reflected, uncertain, not checked, or not applicable. Provide concrete evidence and allow clarifications. Future versions should retain comparable history for the same changes across feed versions.
5. Before a run show ready work and exclusions. Compose compatible changes by trip/date, detect conflicts, and build a separate draft. Keep reasons, assumptions and changed records.
6. Compare original with the exact ZIP to be downloaded using aligned schedule views, changed stop markers, route/date/direction filters, trip IDs, field-level changes and validator findings. Clearly mark partial output, estimates, and unverified geometry.
7. Export the output ZIP with a version, file hashes and activity record. Rebuilding from the original should allow removal of a request.

## Alert modeling principles

See [MODELING_POLICY.md](MODELING_POLICY.md). Facility notices and transient delays generally do not change the schedule. A written minute and a matching active-period boundary within that minute are not automatically a conflict; preserve the actual source period and flag material uncertainty. For trips crossing a window, identify them from the affected location/time and apply the appropriate change to the whole trip. Compatible changes should compose; incompatible changes require review. Preserve or correctly update optional and dependent files, including shapes when present. Never label an estimated path or timetable as agency-provided.

## Security and architecture

- Credentials must be supplied per session, never checked into source or persisted with feeds and activity logs.
- GTFS source data, alerts, and model output are untrusted; validate IDs, references, fields, scope, and output independently.
- Keep source snapshots and immutable output versions distinct; verify downloaded ZIP hashes and actual records.
- Local MCP tools expose deterministic functionality to any compatible host. The web client is optional and must not embed a provider key.
- Public hosting requires secure per-user isolation, authentication, controlled outbound access, resource limits and durable jobs; the included server is local-only.

## Current limitations and priorities

The initial MCP covers schedule queries and one skip check. The prototype writer supports a subset of alert changes. Frequency changes, replacement buses, general reroutes and full shape editing are not yet implemented. Checks can be inconclusive. Independent validator tooling is optional and must be configured separately. Priority: reliable source and change provenance, accurate check evidence, better cross-version history, composable writers, and complete validation of the exact exported feed.
