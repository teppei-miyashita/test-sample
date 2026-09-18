"""State — flat AgentState contract shared by the six EDU-C2-045 pipeline nodes.

ADR-005: State must be a flat TypedDict. LangGraph checkpoints use msgpack
serialization, so only plain JSON-serializable fields are allowed. No credentials,
no Pydantic/dataclass instances, no InvocationContext.

Data-boundary rule (DRAFT-2086): individual student health / identity / contact /
disability / consent / financial data is prohibited in every field below. Only
defined *aggregate* health flags and completeness flags may be carried.

Field producers/consumers are documented inline. Every status field is an enum
string with a safe default chosen to fail toward human review, never toward
silent approval.
"""

from __future__ import annotations

from typing import Any, Dict, List

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Flat excursion-risk-assessment state.

    Inherited from AgentState: user_input, input_context, validated_input, result,
    formatted_output, status, session_id, correlation_id, node_history, error_log,
    hitl_* (unused — HITL disabled). Only agent-specific fields are declared here.

    Enum domains (safe defaults in **bold**):
      request_status              : PENDING | VALIDATED | **REJECTED**
      aggregate_health_risk_flag  : NONE | PRESENT | **UNKNOWN**
      health_record_completeness  : COMPLETE | INCOMPLETE | **UNKNOWN**
      data_source_status          : OK | STALE | **UNAVAILABLE**
      destination_coverage        : FULL | PARTIAL | **NOT_FOUND**
      checklist status values     : COMPLETE | NOT_APPLICABLE | **REQUIRES_REVIEW**
    """

    # ── Step 1 · InputValidationNode (pre_process) ───────────────────────
    # Producer: PreProcessNode. Consumer: Step 2 (history), Step 3 (classification).
    validated_excursion: Dict[str, Any]  # sanitized aggregate request (no PII)
    masked_fields: List[str]  # names of fields masked before state entry
    validation_errors: List[str]  # safe, code-level validation messages
    request_status: str  # PENDING | VALIDATED | REJECTED (default REJECTED)

    # ── Step 2 · ExcursionHistoryRetrievalNode ───────────────────────────
    # Producer: ExcursionHistoryRetrievalNode. Consumer: Step 5 (assessment), Step 6.
    excursion_history_summary: Dict[str, Any]  # aggregate counts only, no records
    incident_history_flags: List[str]  # coded incident categories seen before
    standing_policy_flags: List[str]  # school standing-policy codes
    aggregate_health_risk_flag: str  # NONE | PRESENT | UNKNOWN (default UNKNOWN)
    health_record_completeness: str  # COMPLETE | INCOMPLETE | UNKNOWN (default UNKNOWN)
    data_source_status: str  # OK | STALE | UNAVAILABLE (default UNAVAILABLE)
    data_source_provenance: Dict[str, Any]  # source id/scheme/fetched_at (safe ids only)

    # ── Step 3 · RiskCategoryClassificationNode ──────────────────────────
    # Producer: RiskCategoryClassificationNode. Consumer: Step 4 (RAG), Step 5.
    risk_category_map: Dict[str, Any]  # {category: {applicable, drivers, precedence}}
    classification_unknowns: List[str]  # input values that hit the unknown fallback

    # ── Step 4 · PolicyAndGuidanceRAGNode ────────────────────────────────
    # Producer: PolicyAndGuidanceRAGNode. Consumer: Step 5 (assessment), Step 6.
    guidance_citations: List[Dict[str, Any]]  # [{source, section, corpus_version, uri}]
    retrieval_confidence: float  # 0.0–1.0 aggregate ranking confidence
    destination_coverage: str  # FULL | PARTIAL | NOT_FOUND (default NOT_FOUND)
    corpus_staleness_warning: bool  # True when corpus older than threshold
    corpus_metadata: Dict[str, Any]  # {corpus_version, refreshed_at, threshold_days}
    guidance_source_status: str  # OK | FALLBACK | UNAVAILABLE (default UNAVAILABLE)

    # ── Step 5 · RiskChecklistAssessmentNode ─────────────────────────────
    # Producer: RiskChecklistAssessmentNode. Consumer: Step 6 (brief generation).
    checklist_items: List[Dict[str, Any]]  # [{id, category, title, status, guidance, evidence}]
    checklist_statuses: Dict[str, str]  # {item_id: status enum}
    assessment_narrative_source: str  # LLM | TEMPLATE_FALLBACK

    # ── Step 6 · ExcursionBriefGenerationNode (post_process) ─────────────
    # Producer: PostProcessNode. Consumer: API caller (human reviewer).
    administrator_brief_markdown: str  # Section A — admin risk checklist
    parent_communication_markdown: str  # Section B — parent draft (no PII)
    brief_json: Dict[str, Any]  # structured dual-section output

    # ── Cross-cutting ────────────────────────────────────────────────────
    review_warnings: List[str]  # partial/stale/low-confidence flags (all steps)
    requires_human_review: bool  # always True — output is a review aid
    domain_audit_ids: List[str]  # safe correlation/audit identifiers only
    input_error_message: str | None
    input_error_guidance: List[str]
    generation_mode: str | None
    provider_error_message: str | None
