# Settled relay decisions

These decisions capture explicit user direction and the reviewed implementation
shape. Do not silently reopen them as generic cleanup or family normalization.
If a future task changes one, update the operator README, tests, this record,
and the continuation handoff together.

1. `pyarroyo` is the independently published reusable control library and is
   consumed as a pinned Git submodule. Protocol commands, transports, typed
   samples, catalog generation, and library qualification stay in that child;
   polling, InfluxDB schema, credentials, recovery, and deployment stay here.
2. The relay uses direct sequential, script-like orchestration. `main.py` has no
   application classes, helper functions, or `main()` wrapper. Reintroducing
   those abstractions solely for conventional modularity or test seams would
   contradict the accepted coding shape.
3. `settings.toml` is trusted deployer-owned input. Expected keys are consumed
   directly so invalid or missing values fail near startup. Do not add an
   application settings model, general parser, or validation layer without a
   source requirement or explicit user request.
4. The secret-loading and InfluxDB bootstrap blocks are copied byte-for-byte
   from `imaq-secret/README.md`. This includes the imports in their documented
   locations, the unused query API, the semicolon, and passing the complete
   `AUTH["influxdb"]` table to `InfluxDBClient`. The literal-block test is
   intentional, and its resulting `E402`, `I001`, and `E702` Ruff findings must
   not be auto-fixed.
5. `bucket` and `measurement` are distinct. The fixed measurement is stored in
   each record as `measurement = "arroyo"`; the actual destination is selected
   explicitly by `write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, ...)`.
6. Dry-run means “skip the InfluxDB write,” not “skip credentials or client
   setup.” It loads `imaq-secret/auth.toml`, constructs the InfluxDB client and
   both APIs, performs real controller reads, and prints the mapped records.
7. The relay supports one explicitly configured serial or TCP controller and
   never scans. TEC snapshots are acquired first in listed order, then laser
   snapshots in listed order. Omitted selectors mean the current channel or
   sole sensor; they do not produce invented tags.
8. Every configured snapshot must succeed before one synchronous batch write.
   Partial batches are discarded. Each point uses the timestamp returned by
   the corresponding `pyarroyo` sample after its final query, not a shared
   cycle timestamp or InfluxDB server time.
9. Source recovery is one immediate reconnect, identity verification, and
   complete-batch retry. Identity continuity uses manufacturer, model, and
   serial number. Upload errors remain outside that source-recovery boundary.
10. Continuous failure accounting is cumulative over the process lifetime and
    exits on the third unresolved source or upload failure; success does not
    reset the count. One-shot mode exits nonzero on its first unresolved
    failure.
11. Scheduling uses monotonic cycle-start deadlines and skips catch-up reads
    after an overrun. Shutdown closes both clients and the write API but does
    not send `LOCAL` or another instrument command.
12. The root README is the operator manual. Keep installation, initial dry-run
    and upload testing, continuous usage, exact schema, deployment mention,
    validation status, and troubleshooting there. Contributor context belongs
    under `## Developer's note` or in this `docs/` directory; do not add a
    standalone optional-Supervisor or acquisition/recovery essay to the user
    path.
13. Live evidence is staged. Offline review does not authorize hardware access,
    upload, a continuous run, wrapper execution, or Supervisor activation.
    State-changing Arroyo commands remain outside the current authorization.
14. The address `192.168.50.172` in `settings.toml.template` was explicitly
    chosen as a random syntax example. Never treat it as deployment evidence or
    copy it into a live procedure without operator confirmation.
