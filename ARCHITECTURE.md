# Architecture

- **HTTP and dashboard** use the Python standard library plus static JavaScript. The HTTP handler exposes a closed list of local operations. The UI uses text nodes to display fixture and API content.
- **Job, evidence and materials** reuse the original pure Python pipeline modules. Source-backed facts and generated document versions live in SQLite. Evidence scoring is deterministic and qualification decisions remain visible to the reviewer.
- **Mock integrations** implement a small provider interface and return fixture snapshots. Public startup and source packages are independent of the internal workspace and its account configuration.
- **Application Runner** reuses the checkpointing and review workflow with a simulation-only Playwright adapter. Requests go to a loopback fixture. SQLite keeps step state, scoped choices, attachments and the final review token.
- **Submission and recovery** use a two-stage local receipt flow. The fixture requires a saved submission intent. A unique index prevents repeated mock submissions. Pipeline reconciliation records the result in the audit trail.
- **Visitor sessions** use an opaque browser cookie and a separate temporary SQLite database per visitor. A per-session lock serializes reset and workflow mutations. Mock fixture callbacks require a process-only capability and are routed to the active task's database.
- **Reset** seeds and replaces only the current visitor's database.
- **Health** uses a bounded SQLite read on `/healthz`. It skips the workflow lock so a Playwright run does not delay the host probe.

The shared engine modules were copied from a reviewed source allowlist. The public HTTP layer, provider adapters, fixtures and security tests are edition-specific. Future engine syncs require a new source and history review.
