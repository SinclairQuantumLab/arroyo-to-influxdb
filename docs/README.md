# arroyo-to-influxdb maintainer documentation

The root [`README.md`](../README.md) is the operator manual. This directory is
for cross-thread development context and evidence that should not interrupt the
installation and operation path.

## Documentation map

| When you need to... | Read... |
| --- | --- |
| Continue work in another thread or agent | [`continuation.md`](continuation.md) |
| Review decisions that must not be silently normalized | [`decisions.md`](decisions.md) |
| Review or append sanitized live relay evidence | [`live-validation.md`](live-validation.md) |
| Change or qualify the reusable controller library | [`../pyarroyo/docs/README.md`](../pyarroyo/docs/README.md) |

Keep operator-facing settings, commands, schema, deployment, and
troubleshooting synchronized in the root README. Keep protocol command
coverage, manual anomalies, and library qualification in `pyarroyo`. Never put
credentials, actual endpoints, ports, serial numbers, or private deployment
values in either documentation set.
