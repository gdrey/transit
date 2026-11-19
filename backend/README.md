# GTFS Postgres importer

`import_gtfs.py` is an initial, private migration tool. It copies an unchanged GTFS ZIP into Postgres and indexes a subset of the schedule tables by feed version. The ZIP remains the lossless source; the index is not a replacement for all GTFS files or fields. All values in the initial index are text, including GTFS times and stop sequences, so queries must interpret these explicitly. This is not yet a multi-user backend or an export pipeline.

Install the dependencies in a virtual environment, then set `DATABASE_URL` privately and run:

```sh
python import_gtfs.py /path/to/gtfs.zip --source-url https://example.org/gtfs.zip
```

The import runs in one transaction, computes a SHA-256 checksum, and avoids reimporting the same ZIP. Never commit the connection string or put it in a URL argument. Store only a public feed URL in `--source-url`. Schema changes and imports require a direct Postgres connection; application functions should use a pooled connection and appropriately restricted credentials.

The database is currently intended for development only. Before a public hosted workbench, add per-workspace authorization, immutable object storage, version-scoped query APIs, private upload, job orchestration, alert interpretation, edits, output validation, and end-to-end tests. The current script loads one indexed copy globally and is not suitable for exposing user feeds.
