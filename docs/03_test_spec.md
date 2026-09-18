# Test Specification

## Test Strategy
- Coverage target: 80% line coverage
- Test types: Unit / Integration / E2E

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | |
| TC-02 | SecurityViolationError fires on invalid input | Error raised | |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations (S-5 enforcement moved to CI by agentcore#138) | |
| TC-04 | InvocationContext constructed only via `from_state()` inside nodes | Direct `InvocationContext(...)` construction inside a node is flagged (the entry-point adapter in `src/api/server.py` is exempt — it authenticates the caller before any node runs) | |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from `execute()` body | 0 duplicates |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | 0 overrides |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | 0 overrides |
| TC-08 | `required_trust_level` declared and enforced — dedicated S-1 boundary coverage calls `node(state)` through `__call__` | Insufficient trust → execution refused before `execute()`; proof records no execute call | |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial when domain checks needed | Domain-specific input checks execute correctly (e.g. PII scan on additional fields, consent validation, business rules) | Hook body non-trivial |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial when domain checks needed | Domain-specific output checks execute correctly (e.g. nested credential scan, PII re-check, content filtering, preservation verification) | Hook body non-trivial |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | Domain event emitted on every invocation path | ≥1 per node; solely-delegating trace-correlated `GraphNode` exempt |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures | |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass | |
| PB-3 | L1 template boundary → External service | Real external service connection | Data retrieved | |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations | |
| PB-5 | Checkpoint safety *(conditional)* | When `config/config.yaml` enables checkpointing and agentcore exposes both ingress hooks: inspect payload, metadata, and pending writes for JWT/Pydantic objects and raw ingress | Full-surface inspection pass; otherwise **Auto-waived — checkpointing disabled** or **Auto-waived — framework ingress protection unavailable** | |
| PB-6 | Invoke execution order | `__call__()`: S-1 trust gate → S-4 `node_start` → S-2 `_security_gate_input` → `execute()` → S-3 `_security_gate_output` → S-4 `node_complete` | Order verified | |
| PB-7 | HITL interrupt propagation *(conditional)* | Required only when `config.yaml` sets `hitl.enabled: true`; otherwise record **Auto-waived — non-HITL** | `GraphInterrupt` propagates to the LangGraph engine; `status` is not set to `error` | |

> **Pre-CoE gate checklist:** PB-1 through PB-4 and PB-6 are mandatory. Evaluate PB-5 only after `config/config.yaml` enables checkpointing and installed agentcore exposes both ingress hooks; otherwise record an auto-waiver. Evaluate PB-7 only for HITL-enabled templates; this project records **Auto-waived — non-HITL**.

## Business Logic Tests

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Safety-first health default | Health completeness `UNKNOWN` or `INCOMPLETE` | Every health item is `REQUIRES_REVIEW` | Automated unit coverage |
| BL-02 | Deterministic classification | Known and unknown destination/transport/activity values | Rule-table union plus safe unknown fallback | Automated unit coverage |
| BL-03 | Optional LLM failure | Missing client, timeout, or malformed response | Checklist statuses unchanged; narrative uses `TEMPLATE_FALLBACK` | Automated unit coverage |
| BL-04 | Invocation-scoped LLM | Invocation provides Azure credentials | Azure client is created inside the node and provider failure falls back safely | Proof-of-boundary coverage |

## Test Execution Summary
- Execution date: 2026-08-18
- Total tests: 51
- Pass: 48 / Fail: 0 / Skip: 3 (PB-5 checkpointing and PB-7 HITL auto-waivers)
- Coverage: Not collected by `check-local.sh`
