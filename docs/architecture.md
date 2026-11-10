# Architecture and engineering tradeoffs

Probe separates test submission, execution, the manual demo business, and temporary test data. The [main README](../README.md) contains the system diagram and startup commands.

## Components and code map

| Component | Responsibility | Code |
| --- | --- | --- |
| React workbench | Select cases; submit/control runs; inspect reports and data | `frontend/src/App.tsx`, `RunReport.tsx`, `DatabaseView.tsx` |
| Platform API | Validate submissions, query history, expose reports and demo operations | `probe/platform/main.py` |
| Case catalog | Collect real pytest cases and validate a saved selection | `probe/platform/case_catalog.py`, `collection.py` |
| Run repository | Transactions for idempotency, claims, leases, cancellation, completion | `probe/platform/repository.py` |
| Worker and executor | Own attempts, spawn pytest, observe control state, clean and recover | `probe/platform/runner.py`, `execution.py`, `processes.py` |
| HTTP test service | Bind an ephemeral socket and run a dedicated order API | `probe/http_testing.py`, `http_service.py` |
| Evidence and reports | Capture HTTP/database state; preserve it through JUnit and JSONB | `probe/test_evidence.py`, `platform/reports.py` |
| Demo order service | Inventory transactions and payment/cancellation rules | `probe/main.py`, `service.py`, `models.py` |

## Case collection and submission

The catalog runs `pytest --collect-only`, preserving parameterized node IDs, source locations, and PostgreSQL requirements. Collection imports modules but does not execute fixtures. A fingerprint covers test files, relevant fixtures, suite configuration, and the collection plugin.

A submission saves the chosen case IDs and catalog revision. The Worker executes that saved selection, including for an all-cases submission. New cases added after submission do not silently expand queued work. A linked rerun is a new run; it preserves its source relationship and original selection while validating the current catalog.

Tests come from a trusted suite allowlist. The API does not accept a shell command, arbitrary uploaded script, or arbitrary file path.

Validation: [case selection tests](../tests/platform/test_case_selection.py) and [rerun tests](../tests/platform/test_rerun.py).

## PostgreSQL queue and submission idempotency

Queued runs are persisted in `probe_platform`. Claiming uses a row lock with `SKIP LOCKED`, allowing local Workers to skip work already locked by another claimant. Attempt creation, ownership, lease assignment, and the transition to running commit together.

An `Idempotency-Key` is unique across submissions. Its stored request fingerprint includes execution parameters, selected case IDs, and rerun source where applicable. Replaying identical content returns the existing run; reusing the key with conflicting content returns 409.

PostgreSQL was chosen because the project already needs it, and claim/state changes can share a transaction. There is no throughput benchmark justifying a separate broker yet. Database queueing does not establish exactly-once execution or undo external side effects.

Validation: [run API tests](../tests/platform/test_runs.py), [reliability tests](../tests/platform/test_reliability.py), and [history tests](../tests/platform/test_history.py).

## Leases, fencing, and recovery

A heartbeat extends ownership only if the Worker, active attempt, state, and current lease still match. An expired lease cannot be revived by its previous owner. Completion applies equivalent checks inside a transaction before committing the result.

On lease expiry, a replacement Worker takes recovery ownership before stopping the old attempt and cleaning its resources. Worker loss can produce a bounded new attempt. Assertion failures, startup errors, timeouts, and cancellations are not automatically retried into success.

Cancellation is persisted. Queued cancellation creates no execution result; running cancellation finishes after stopping and cleanup. The execution watchdog also checks parent liveness, ownership, lease, timeout, and cancellation. PostgreSQL availability remains part of recovery; this is not a proof of behavior under every network partition.

Validation: [reliability tests](../tests/platform/test_reliability.py), [HTTP execution tests](../tests/platform/test_http_execution.py), and [demo-suite lifecycle tests](../tests/platform/test_demo_suite.py).

## Process, directory, and database isolation

Each attempt has a fresh process/session, working directory, pytest temporary directory, and artifact paths. PostgreSQL suites use `probe_test.run_<attempt-hex>`; direct pytest runs use separate `case_<random-hex>` schemas. Fixtures rebuild business tables/data for each case.

The `demo_orders` suite also creates platform test tables. Its Worker-owned space lives in `probe_platform_test.platform_case_<attempt-hex>`. An ownership marker is written before schema creation, so another Worker can discover cleanup responsibilities after an interruption. Both schema names are tied to the attempt identity.

The execution process continues to import trusted source from the repository. It has no container resource limits, filesystem sandbox, or tenant security boundary. `cleanup_done` confirms active processes and test schemas were cleaned; retained files are not covered by that flag.

Validation: [database fixtures](../tests/conftest.py), [platform fixtures](../tests/platform/conftest.py), and [demo-suite tests](../tests/platform/test_demo_suite.py).

## Real TCP requests

HTTP tests bind a loopback socket with port zero and pass the already-bound descriptor to their service process. This avoids a check-free-port/rebind race. Each service uses the case/attempt's test schema and records its address, lifecycle, and logs.

TestClient/ASGI tests validate HTTP behavior in process. TCP tests additionally exercise a listening process, socket connection, read timeout, and service cleanup. A request with no response records a null HTTP status and the transport exception; it is not rewritten as an invented server response.

Validation: [HTTP order tests](../tests/http/test_orders_http.py) and [platform HTTP lifecycle tests](../tests/platform/test_http_execution.py).

## Evidence chain

Tests collect request/response data and database state before assertions and fixture cleanup. Explicit expected/actual checks and the evidence document are stored as pytest user properties, carried into JUnit XML, and parsed into JSONB reports.

HTTP data and database snapshots are separate observations around a request, not a complete audit ledger or an atomic transaction trace. Concurrent tests capture batch-level state where individual attribution is not possible. Selected headers and common sensitive JSON keys are filtered; arbitrary test logs are not guaranteed free of secrets.

The pytest outcome is authoritative. Captured checks cannot turn a failed case into a pass. The UI may conservatively extract supported assertion comparisons, while retaining the original failure output for unsupported or ambiguous cases.

Validation: [evidence contract](../tests/test_evidence_contract.py), [case evidence](../tests/platform/test_case_evidence.py), and [demo-suite failure evidence](../tests/platform/test_demo_suite.py).

## Demo business and migration boundaries

The manual order API uses `probe`; automated business tests use `probe_test`. A conditional stock update and order insert share a transaction. Payment events use a unique stable event ID; order locks coordinate payment/cancellation. Prices are integer cents, and UI-facing big integers use decimal strings.

Order tables use explicit initialization and an additive SQL availability migration. Platform tables use Alembic through `0004_rerun_source`. These are distinct migration mechanisms; platform `alembic check` does not validate order-table drift.

Validation: [order tests](../tests/test_orders.py), [product management](../tests/platform/test_product_management.py), and [demo interfaces](../tests/platform/test_demo_orders.py). The Chinese [order design](design.md) explains stock rollback and the selected order lock in more detail.

## Deliberate boundaries

Current verification covers the local project, including multiple local Workers and controlled failures. There is no production deployment claim, capacity result, hosted service, formal committed Playwright suite. A [GitHub Actions run](https://github.com/Tsy-iii/Probe/actions/runs/42786973058) has verified the regression and frontend build on Ubuntu 24.04. The executor requires POSIX; other Linux configurations remain unverified and Windows is not supported by the current process-control path.
