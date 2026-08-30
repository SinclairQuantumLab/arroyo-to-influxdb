# Live validation record

This is the sanitized, append-only evidence log for the complete relay. Library
hardware evidence is recorded separately in
[`../pyarroyo/docs/live-validation.md`](../pyarroyo/docs/live-validation.md).

## Recording rules

- Record the UTC date, parent commit, pinned `pyarroyo` commit, transport class,
  sanitized model/firmware facts, exact command with endpoint values replaced
  by placeholders, evidence stage, and pass/fail result.
- Never record an actual device serial number, port, hostname, IP address,
  credential, token, bucket secret, or private deployment value.
- Keep read-only source qualification, dry-run mapping, one authorized upload
  and query-back, continuous foreground operation, startup wrapper, and
  Supervisor operation as separate evidence.
- A failure is evidence. Preserve its exception class and sanitized symptom;
  do not probe other ports, hosts, channels, sensors, or commands to make it
  pass.
- Do not record state-changing tests here unless a separate bounded procedure
  was explicitly authorized.

## Recorded runs

None. No live controller read, InfluxDB upload/query-back, startup wrapper, or
Supervisor run has been performed in this workspace.

## Entry template

Copy this section for a real run and replace every placeholder. Use
`NOT_TESTED` rather than implying that a later gate passed.

```markdown
### <UTC_DATE> - <SANITIZED_MODEL> - <EVIDENCE_STAGE>

- Parent commit: `<FULL_PARENT_COMMIT_SHA>`
- pyarroyo commit: `<FULL_CHILD_COMMIT_SHA>`
- Transport: `<USB_VIRTUAL_COM | RS232 | TCP>`
- Identity: manufacturer `<MANUFACTURER>`, model `<MODEL>`, firmware
  `<FIRMWARE>`, build `<BUILD>`, serial redacted
- Operator-confirmed capabilities: `<CAPABILITIES>`
- Invocation: `uv run python main.py --settings <SANITIZED_PATH> <OPTIONS>`
- Source identity/read: `<PASS | FAIL | NOT_TESTED>`
- Channel restoration: `<PASS | FAIL | NOT_TESTED>`
- Record schema/types/units/timestamps: `<PASS | FAIL | NOT_TESTED>`
- InfluxDB write: `<PASS | FAIL | NOT_TESTED>`
- InfluxDB query-back: `<PASS | FAIL | NOT_TESTED>`
- Continuous foreground run: `<PASS | FAIL | NOT_TESTED>`
- Startup wrapper: `<PASS | FAIL | NOT_TESTED>`
- Supervisor service/logs: `<PASS | FAIL | NOT_TESTED>`
- Result: `<PASS | FAIL>`
- Sanitized observations: `<OBSERVATIONS>`
```
