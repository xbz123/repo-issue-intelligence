# Protocol v2 local data protection

PR2B supplies an explicit Store and database tools, not a runnable V2 Agent.
The existing Agent CLI/API continue to use V1 by default. No migration runs on
startup, and no command automatically combines a beta database with legacy data.

## Create or inspect

Use a dedicated owner-only directory. The tools create missing destination
directories with mode `0700` and databases with mode `0600`. They refuse an
existing public destination directory instead of changing its permissions.

```sh
uv run rii agent-db inspect path/to/agent.sqlite3
uv run rii agent-db create-v2 --destination private/v2.sqlite3
uv run rii agent-db migrate --source path/to/legacy.sqlite3 --destination private/imported.sqlite3
```

`inspect` reads an existing file; it does not create a missing database. Creating
or migrating requires a new destination. Existing files, symlinks and migration
receipts are never overwritten. The V2 Store validates an explicit target rather
than creating tables. A legacy writer directed at V2 refuses before initialization
or writes, including when an existing writer instance is reused.

Destination ancestors follow the Store policy: user-created symlink directories
are rejected before creating subdirectories or publishing files. The existing
system `/tmp` and `/var` aliases remain accepted. Resolve an intended alias
explicitly before selecting the destination; creation does not silently change
the accepted path to one the Store would refuse.

## Migration and provenance

Migration uses SQLite backup into a private staging directory, validates the
legacy copy, and upgrades that copy transactionally using the sole migration DDL.
Only the complete database is published. The source's version, payload and V1
write behavior are unchanged; this is a point-in-time copy, not a live mirror.

Imported legacy tables are read-only, including direct INSERT/UPDATE/DELETE and
REPLACE paths. Legacy projection preserves the original JSON and labels the old
protocol. Evidence is `unavailable`: an old snapshot or requested model is not
converted into sealed evidence, a recoverable V2 execution or reported telemetry.

The private adjacent `<database>.legacy.json` receipt records the source path,
source file identity, migration time and destination identity. Keep it with the
database. Missing provenance is reported as unavailable; mismatched receipts are
rejected. A copied database has a different identity, so copying a receipt does
not fabricate provenance for that copy.

The receipt is published before the database. If the process stops between these
two steps, only the receipt may remain; use a new destination rather than silently
reusing or deleting that artifact. Ordinary pre-publication errors clean up only
temporary files and the receipt created by that invocation. No historical run,
evidence, attempt, review, source or backup is automatically removed.

Publication syncs new parent-directory entries, then the receipt entry, then the
database entry. The command reports failure if a required directory `fsync`
fails. If the database link is already visible when this happens, it is retained
(with its receipt for migration); inspect it rather than retrying over the same
destination. Pre-database-link cleanup removes only this invocation's receipt
and syncs that removal. Tests check ordering and injected I/O failures, not actual
power-loss behavior; durability still depends on filesystem/hardware guarantees.

Source identity checks conservatively refuse changes while opening a snapshot,
including source-path and parent-directory replacement. Unrelated directory
metadata changes can also cause refusal. SQLite can create a missing `-shm` index
when reading a WAL database; a stable retry is supported, without modifying source
db/WAL contents. File identity checks depend on reliable filesystem metadata and
do not isolate data from the local filesystem administrator.

## Evidence and diagnostics

Sealed evidence retains exact content, ordered IDs and collection/truncation
metadata. The explicit V2 collector uses a fixed repository view and whole-line
truncation; the client-facing evidence lookup returns the stored snippets without
another truncation pass. Evidence is stored inside the private database, not in
an additional public source directory. Treat the database and its backups as
sensitive source-code artifacts; this version does not provide encryption at rest.

V2 candidates wholly beyond the captured file are skipped before context
expansion, rather than being clamped to unrelated last-line evidence. Both
collectors reject an explicitly closed RepositoryView, even if its former
directory still exists or has been recreated; the default V1 checkout path is
unchanged. Conflicting duplicated output-token/timeout request parameters and
budgets are rejected at configuration capture/read and before creating a run,
including caller-created model copies. No existing configuration is rewritten
automatically; matching duplicates and budget-only requests remain supported.
New captures record `budget.output_tokens` and `budget.timeout_seconds` origins
separately, so an omitted budget remains distinguishable from explicit null after
JSON roundtrips. Older records lacking those markers retain the legacy origin
interpretation and are not silently rewritten; a conflicting/ambiguous record
must be investigated rather than assigned an invented request value.

V2 traces accept only small, typed diagnostic metadata and references. Full maps,
source text, arbitrary provider responses and credentials do not belong in traces.
CLI database commands report operation/kind/version only, and suppress raw SQLite
or filesystem error text. Provenance paths are local private receipt contents,
not routine console diagnostics. Retention is explicit: users decide separately
when to archive or delete historical data; this release sets no automatic TTL.

HTTP evidence access, authorization, recovery/retry and review services remain in
their later R5 work packages. Local Store availability grants no provider-transfer
permission and does not change the G0/G1 release gates.
