# Template Design Specification — EDU-C2-045

## Position in AgentCore Architecture

- **Agent Class**: `Graph` (module `src.graph.graph`) — L1-direct inheritance from `AgentBaseGraph`
- **L1 Base**: AgentBaseGraph
- **Three-Layer Separation**:
  - State: `src/schemas/state.py` — flat `TypedDict` (no Pydantic — msgpack incompatible)
  - Node: L1 `FunctionNode` inheritance — `execute(self, state: dict) -> dict` override only
  - Graph: composition — `register_nodes()` for the outer graph + inner `DomainWorkflowGraph`

## Architecture Overview

### Node Configuration

| Node slot | Class | Step | Responsibility | Trust | Inherits |
|-----------|-------|------|---------------|-------|----------|
| initialize | InitializeNode | — | Framework state init | — | default |
| pre_process | `PreProcessNode` | 1 | Input schema validation, S-2 PII rejection, financial masking | `VERIFIED_EXTERNAL` | `FunctionNode` |
| main | `DomainWorkflowGraphNode` | 2–5 | Inner pipeline wrapper | `VERIFIED_EXTERNAL` | `GraphNode` |
| post_process | `PostProcessNode` | 6 | Dual-section brief generation, S-3 output gate | `VERIFIED_EXTERNAL` | `FunctionNode` |
| finalize | FinalizeNode | — | Framework state finalize | — | default |

**Inner `DomainWorkflowGraph` nodes (all `ANONYMOUS` — trust verified once at boundary):**

| Node key | Class | Step | Responsibility |
|----------|-------|------|---------------|
| excursion_history | `ExcursionHistoryRetrievalNode` | 2 | Aggregate-only history + health flags from school data source |
| risk_classification | `RiskCategoryClassificationNode` | 3 | Deterministic rule-table category mapping |
| policy_guidance_rag | `PolicyAndGuidanceRAGNode` | 4 | VectorRAG over MEXT/policy/destination corpora |
| risk_checklist_assessment | `RiskChecklistAssessmentNode` | 5 | Deterministic checklist status + LLM narrative only |

### Data Flow

```
START → initialize → pre_process (Step 1: validate + mask)
                         │
                    main (DomainWorkflowGraphNode)
                         │
                    ┌────▼────────────────────────────────────────┐
                    │  excursion_history (Step 2: retrieve)       │
                    │  → risk_classification (Step 3: classify)   │
                    │  → policy_guidance_rag (Step 4: RAG)        │
                    │  → risk_checklist_assessment (Step 5: assess)│
                    └────────────────────────────────────────────┘
                         │
                    post_process (Step 6: generate brief + S-3)
                         │
                    finalize → END
```

No autonomous loop. No conditional branches within the domain pipeline. A retrieval
failure degrades to review-only output and can never produce an approval-ready brief.

### State Definition

All fields are in `src/schemas/state.py` (flat `TypedDict`). Enum safe-defaults are chosen to
fail toward human review, never toward silent approval.

| Field | Type | Producer | Consumer | Enum / Default |
|-------|------|----------|---------|---------------|
| `validated_excursion` | `Dict[str, Any]` | PreProcessNode | Steps 2,3 | — |
| `masked_fields` | `List[str]` | PreProcessNode | PostProcessNode | — |
| `validation_errors` | `List[str]` | PreProcessNode | PostProcessNode | — |
| `request_status` | `str` | PreProcessNode | PostProcessNode | `PENDING\|VALIDATED\|REJECTED` (default REJECTED) |
| `excursion_history_summary` | `Dict[str, Any]` | ExcursionHistoryRetrievalNode | Step 5,6 | — |
| `incident_history_flags` | `List[str]` | ExcursionHistoryRetrievalNode | Step 5,6 | — |
| `standing_policy_flags` | `List[str]` | ExcursionHistoryRetrievalNode | Step 5,6 | — |
| `aggregate_health_risk_flag` | `str` | ExcursionHistoryRetrievalNode | Step 5 | `NONE\|PRESENT\|UNKNOWN` (default UNKNOWN) |
| `health_record_completeness` | `str` | ExcursionHistoryRetrievalNode | Step 5 | `COMPLETE\|INCOMPLETE\|UNKNOWN` (default UNKNOWN) |
| `data_source_status` | `str` | ExcursionHistoryRetrievalNode | Step 6 | `OK\|STALE\|UNAVAILABLE` (default UNAVAILABLE) |
| `data_source_provenance` | `Dict[str, Any]` | ExcursionHistoryRetrievalNode | Step 6 | — |
| `risk_category_map` | `Dict[str, Any]` | RiskCategoryClassificationNode | Steps 4,5 | — |
| `classification_unknowns` | `List[str]` | RiskCategoryClassificationNode | Step 6 | — |
| `guidance_citations` | `List[Dict[str, Any]]` | PolicyAndGuidanceRAGNode | Steps 5,6 | — |
| `retrieval_confidence` | `float` | PolicyAndGuidanceRAGNode | Step 6 | 0.0 |
| `destination_coverage` | `str` | PolicyAndGuidanceRAGNode | Steps 5,6 | `FULL\|PARTIAL\|NOT_FOUND` (default NOT_FOUND) |
| `corpus_staleness_warning` | `bool` | PolicyAndGuidanceRAGNode | Step 6 | `True` |
| `corpus_metadata` | `Dict[str, Any]` | PolicyAndGuidanceRAGNode | Step 6 | — |
| `guidance_source_status` | `str` | PolicyAndGuidanceRAGNode | Step 6 | `OK\|FALLBACK\|UNAVAILABLE` (default UNAVAILABLE) |
| `checklist_items` | `List[Dict[str, Any]]` | RiskChecklistAssessmentNode | Step 6 | — |
| `checklist_statuses` | `Dict[str, str]` | RiskChecklistAssessmentNode | Step 6 | — |
| `assessment_narrative_source` | `str` | RiskChecklistAssessmentNode | Step 6 | `LLM\|TEMPLATE_FALLBACK` |
| `administrator_brief_markdown` | `str` | PostProcessNode | API caller | — |
| `parent_communication_markdown` | `str` | PostProcessNode | API caller | — |
| `brief_json` | `Dict[str, Any]` | PostProcessNode | API caller | — |
| `review_warnings` | `List[str]` | All nodes (accumulated) | Step 6, API | — |
| `requires_human_review` | `bool` | PreProcessNode, PostProcessNode | API caller | always `True` |
| `domain_audit_ids` | `List[str]` | Any node | Audit | — |

**State Constraints:**
- Flat `TypedDict` only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State
- `InvocationContext` resolved at runtime inside `execute()` — never stored in State
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)
- Individual student health/identity/contact/disability/consent/financial data is prohibited

**Data classification:**

| Class | Examples | Permitted in state? |
|-------|---------|---------------------|
| Public guidance | MEXT safety policy citations, corpus section URIs | Yes |
| School operational | Destination, dates, year group, student count, transport mode | Yes (aggregate/sanitized) |
| Aggregate health flags | `aggregate_health_risk_flag`, `health_record_completeness` | Yes — codes only |
| Individual student data | Names, health records, consent forms, contact details, disability data | **No — prohibited at every boundary** |

## Framework Utilization

### Shared Components Used
- [x] `InvocationContext` — `from_state(state)` in connector nodes requiring secrets (`ctx.secrets.require()`)
- [ ] `ConnectionPolicy` — connector adapters own bounded request policy; no direct framework policy object is stored in State
- [x] `SecurityViolationError` — raised by `_extra_security_gate_input` and `_extra_security_gate_output`
- [x] S-2: `_extra_security_gate_input()` in `PreProcessNode` — rejects individual student health/PII in free-text fields before anything enters state
- [x] S-3: `_extra_security_gate_output()` in `PostProcessNode` — blocks individual student data from either output section; verifies mandatory non-approval disclaimer
- [x] S-4: `emit_trace_event()` — at least one domain event per `execute()` in every node

### Dependency/Config Contract

| Dependency | Kind | Configured via | Credential |
|-----------|------|---------------|-----------|
| School data source | REST/schooldata adapter | `school_data_source_ref` in `config.yaml` | `SCHOOL_DATA_TOKEN` via `ctx.secrets.require()` |
| Qdrant guidance corpus | VectorDB | `guidance_collection` in `config.yaml` | `QDRANT_API_KEY` via `ctx.secrets.require()` |
| Narrative LLM | Azure OpenAI | Client constructed per invocation by `src/services/llm_runtime.py` | Three Azure credentials via `InvocationContext`; provider failure uses deterministic fallback |

### Failure/Default Behaviour

| Failure | Node | Safe default |
|---------|------|-------------|
| School data outage | ExcursionHistoryRetrievalNode | `health_record_completeness=UNKNOWN`, `aggregate_health_risk_flag=UNKNOWN`, warning added |
| Unknown input value (destination/transport/activity) | RiskCategoryClassificationNode | fallback categories applied, recorded in `classification_unknowns` |
| Guidance corpus unavailable | PolicyAndGuidanceRAGNode | general MEXT fallback citations, `destination_coverage=NOT_FOUND`, warning added |
| Destination not in corpus (PARTIAL/NOT_FOUND) | PolicyAndGuidanceRAGNode | per-category MEXT fallback, coverage warning |
| Stale corpus | PolicyAndGuidanceRAGNode | `corpus_staleness_warning=True`, warning added |
| LLM narrative timeout/error | RiskChecklistAssessmentNode | template fallback narrative, `assessment_narrative_source=TEMPLATE_FALLBACK` |
| Health completeness not COMPLETE | RiskChecklistAssessmentNode | all health/medical items forced to `REQUIRES_REVIEW` |
| Request rejected at Step 1 | PreProcessNode | pipeline terminates with `REJECTED` brief, no domain data in output |

### Composition Pattern

- **Pattern**: Cat 2 — `AgentBaseGraph` (outer) + `BaseGraph` inner workflow
- **Composition target**: `DomainWorkflowGraph` in the `main` slot via `DomainWorkflowGraphNode` (GraphNode)
- **Error propagation strategy**: `propagate` — a subgraph error must never be silently converted to a success

## EU AI Act Art.13 Design-Time Evidence

The proposal declares this intended use outside Annex III scope. The transparency
controls remain part of the design as defense in depth.

| Evidence item | Design reference / description |
|---------------|--------------------------------|
| Intended purpose and operating context | Decision-support for school staff preparing excursions; output is reviewed outside the graph before approval or parent communication. |
| System capabilities and limitations | Produces a checklist and communication draft; cannot approve, send messages, mutate source records, or infer individual medical risk. |
| User-facing transparency information | Every brief contains a non-approval disclaimer, citations/coverage metadata, warnings, and narrative-source provenance. |
| Human oversight mechanism | `requires_human_review` is always true; unresolved evidence and unavailable sources fail toward `REQUIRES_REVIEW`. |

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework.*`, `shared.*`, `langgraph.*`, own `src.*` only

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | `AgentBaseGraph` | `AutonomousBaseGraph` | `AgentBaseGraph` | The workflow is a fixed six-step linear pipeline — no autonomous termination loop needed |
| Inner node trust level | `VERIFIED_EXTERNAL` on all nodes | `ANONYMOUS` on inner nodes | `ANONYMOUS` for inner, `VERIFIED_EXTERNAL` for outer boundary | Trust-trap anti-pattern: trust is verified once at `PreProcessNode`; re-declaring it inside the inner graph falsely implies re-verification |
| Health default direction | Fail-open (NOT_APPLICABLE when unknown) | Fail-closed (REQUIRES_REVIEW when unknown) | Fail-closed | Safety-first: absent/unconfirmed health data must surface for human review, never be silently excluded |
| LLM scope | LLM controls both status and narrative | LLM for narrative only, deterministic status | Deterministic status + LLM narrative | Status decisions must be reproducible and auditable; LLM output is untrusted for control flow |
| LLM dependency delivery | Node resolves a provider credential | Server constructs client and graph injects it | Explicit constructor injection | Matches the framework node contract; nodes have no graph-config back-reference and no provider object enters State |
| Guidance unavailability | Block pipeline | Degrade to MEXT fallback | Degrade to MEXT fallback | Preserves review-only output; human reviewer sees coverage gaps rather than a hard error |
