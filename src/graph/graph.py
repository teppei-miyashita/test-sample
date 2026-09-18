"""Graph — outer Cat 2 AgentBaseGraph for EDU-C2-045.

L1-direct inheritance from AgentBaseGraph (NOT DocGenerationAgent). The fixed
backbone maps the approved six-step linear pipeline:

  pre_process  → Step 1  InputValidationNode         (VERIFIED_EXTERNAL, S-2)
  main         → Steps 2–5 DomainWorkflowGraph        (inner, ANONYMOUS)
  post_process → Step 6  ExcursionBriefGenerationNode (VERIFIED_EXTERNAL, S-3)

No autonomous loop, no conditional branches, no source-system write-back. A
retrieval failure degrades to a review-only brief and can never become an
approval-ready output.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.graph.base_graph import BaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class DomainWorkflowGraphNode(GraphNode):
    """Wraps the inner DomainWorkflowGraph in the `main` slot (Steps 2–5)."""

    # Fail fast: a subgraph error must never be silently converted to a success.
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        llm: Any = None,
        **kwargs: Any,
    ) -> None:
        self._config = config or {}
        self._llm = llm if llm is not None else (config or {}).get("llm")
        super().__init__(**kwargs)

    def get_subgraph(self) -> BaseGraph:
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> dict[str, Any]:
        """Pass the sanitized excursion payload into the inner pipeline.

        Returns a structured dict (not a bare string): every inner node needs the
        validated aggregate fields, and only sanitized, non-PII values cross this
        boundary.
        """
        return {"validated_excursion": state.get("validated_excursion", {})}

    def execute(self, state: AgentState) -> dict[str, Any]:
        if state.get("input_error_message"):
            return {"status": AgentStatus.SUCCESS.value}
        return cast(dict[str, Any], super().execute(state))

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        """Map inner-graph results into outer state (explicit field mapping only)."""
        sub_result = sub_result or {}
        outer_warnings = state.get("review_warnings", []) or []
        inner_warnings = sub_result.get("review_warnings", []) or []
        merged_warnings = outer_warnings + [w for w in inner_warnings if w not in outer_warnings]
        return {
            "excursion_history_summary": sub_result.get("excursion_history_summary", {}),
            "incident_history_flags": sub_result.get("incident_history_flags", []),
            "standing_policy_flags": sub_result.get("standing_policy_flags", []),
            "aggregate_health_risk_flag": sub_result.get("aggregate_health_risk_flag", "UNKNOWN"),
            "health_record_completeness": sub_result.get("health_record_completeness", "UNKNOWN"),
            "data_source_status": sub_result.get("data_source_status", "UNAVAILABLE"),
            "data_source_provenance": sub_result.get("data_source_provenance", {}),
            "risk_category_map": sub_result.get("risk_category_map", {}),
            "classification_unknowns": sub_result.get("classification_unknowns", []),
            "guidance_citations": sub_result.get("guidance_citations", []),
            "retrieval_confidence": sub_result.get("retrieval_confidence", 0.0),
            "destination_coverage": sub_result.get("destination_coverage", "NOT_FOUND"),
            "corpus_staleness_warning": sub_result.get("corpus_staleness_warning", True),
            "corpus_metadata": sub_result.get("corpus_metadata", {}),
            "guidance_source_status": sub_result.get("guidance_source_status", "UNAVAILABLE"),
            "checklist_items": sub_result.get("checklist_items", []),
            "checklist_statuses": sub_result.get("checklist_statuses", {}),
            "assessment_narrative_source": sub_result.get("assessment_narrative_source", "TEMPLATE_FALLBACK"),
            "review_warnings": merged_warnings,
            "status": sub_result.get("status", AgentStatus.SUCCESS.value),
        }

    def _parent_config(self) -> dict[str, Any]:
        cfg = self._config
        # The LLM is an in-memory runtime dependency, never a State field.
        return {
            "guidance_collection": cfg.get("guidance_collection"),
            "school_data_source_ref": cfg.get("school_data_source_ref"),
            "llm": self._llm,
        }


class Graph(AgentBaseGraph):
    """Outer Cat 2 AgentBaseGraph for EDU-C2-045 (SchoolExcursionRiskAssessmentAgent)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    @property
    def name(self) -> str:
        return "edu_c2_045"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects initialize + finalize
        self._nodes["pre_process"] = PreProcessNode()  # FunctionNode — no args
        self._nodes["main"] = DomainWorkflowGraphNode(config=self.config, llm=self.config.get("llm"))
        self._nodes["post_process"] = PostProcessNode(
            llm=self.config.get("llm"),
            config=self.config,
        )

    def get_output(self, state: AgentState) -> dict[str, Any]:
        output = cast(dict[str, Any], super().get_output(state))
        output["generation_mode"] = state.get("generation_mode")
        output["provider_error_message"] = state.get("provider_error_message")
        _set_marketplace_guidance(output, state, "School excursion risk assessment request")
        return output

    # add_edges() intentionally NOT overridden — backbone wiring is framework-owned.


def _set_marketplace_guidance(output: dict[str, Any], state: AgentState, subject: str) -> None:
    context = state.get("input_context")
    message = state.get("input_error_message")
    if not (isinstance(context, dict) and "conversation_history" in context and message):
        return
    lines = [f"{subject} could not be processed.", "", f"Reason: {message}"]
    guidance = state.get("input_error_guidance")
    if isinstance(guidance, list) and guidance:
        lines.extend(["", "How to continue:"])
        lines.extend(f"- {item}" for item in guidance)
    output["output"] = "\n".join(lines)
