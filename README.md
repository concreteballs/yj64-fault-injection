# YJ-64 Fault Injection

A controlled Stage 2 diagnostic experiment.

This repository intentionally crashes the **base application process** after startup so that the embedded Internal Diagnostic Agent can detect the failure and report it through the existing Diagnostic Bridge to the external YJ-64 monitor.

Expected sequence:

1. External YJ-64 monitor is running.
2. Fault Injection APK starts.
3. Internal Diagnostic Agent starts first.
4. Agent runs self-diagnostics and sends reports.
5. Agent launches the base application.
6. Base application waits five seconds and raises a deliberate RuntimeError.
7. The base application's main process terminates.
8. The internal agent remains alive in its service process.
9. The agent detects that the target main process is gone.
10. The agent sends a target_process_terminated report through the Diagnostic Bridge.
11. The external monitor records the report.

The deliberate exception is explicitly marked:
'FAULT_INJECTION: deliberate YJ-64 base application crash'

## Important test boundary

This experiment proves detection/reporting of a deliberately terminated target process. It does not yet claim that Android's native crash reason, stack trace, ANR diagnostics, or tombstone are captured. Those are separate diagnostic layers to add after this test.

## Development bridge

- host: 127.0.0.1
- port: 9333
- schema: yj64.diagnostic.v1
- token: yj64-dev-bridge-v1

The HTTPS endpoint remains intentionally empty for this development test.
