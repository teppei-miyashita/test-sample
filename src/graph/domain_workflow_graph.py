"""DomainWorkflowGraph — inner linear pipeline for EDU-C2-045 (Steps 2→5).

Strictly linear BaseGraph invoked by DomainWorkflowGraphNode. No branches, no
loops. All nodes here are ANONYMOUS: trust was verified at the PreProcessNode
boundary in the outer graph.

    START → excursion_history → risk_classification → policy_guidance_rag
          → risk_checklist_assessment → END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus

from src.nodes.excursion_history_retrieval_node import ExcursionHistoryRetrievalNode
from src.nodes.policy_and_guidance_rag_node import PolicyAndGuidanceRAGNode
from src.nodes.risk_category_classification_node import RiskCategoryClassificationNode
from src.nodes.risk_checklist_assessment_node import RiskChecklistAssessmentNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow: retrieval → classification → RAG → assessment."""

    @property
    def name(self) -> str:
        return "edu_c2_045_excursion_workflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        """No mandatory construction config: runtime parameters are loaded from
        config/config.yaml by the service layer; credentials resolve per-node via
        ctx.secrets.require(). Nothing to validate at compile time."""
        return None

    def register_nodes(self) -> None:
        # No super() call — BaseGraph.register_nodes() is abstract. Inner nodes only.
        self._nodes["excursion_history"] = ExcursionHistoryRetrievalNode()
        self._nodes["risk_classification"] = RiskCategoryClassificationNode()
        self._nodes["policy_guidance_rag"] = PolicyAndGuidanceRAGNode()
        self._nodes["risk_checklist_assessment"] = RiskChecklistAssessmentNode(llm=self.config.get("llm"))

    def add_edges(self) -> None:
        self._sg.add_edge(START, "excursion_history")
        self._sg.add_edge("excursion_history", "risk_classification")
        self._sg.add_edge("risk_classification", "policy_guidance_rag")
        self._sg.add_edge("policy_guidance_rag", "risk_checklist_assessment")
        self._sg.add_edge("risk_checklist_assessment", END)

    def route(self, state: AgentState) -> str:
        """Required by BaseGraph ABC. The topology is linear (never branches); this
        exists only to satisfy the abstract contract."""
        if state.get("status") == AgentStatus.ERROR.value:
            return str(END)
        return "risk_checklist_assessment"

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Shape sub_result for DomainWorkflowGraphNode.merge_output()."""
        keys = (
            "excursion_history_summary",
            "incident_history_flags",
            "standing_policy_flags",
            "aggregate_health_risk_flag",
            "health_record_completeness",
            "data_source_status",
            "data_source_provenance",
            "risk_category_map",
            "classification_unknowns",
            "guidance_citations",
            "retrieval_confidence",
            "destination_coverage",
            "corpus_staleness_warning",
            "corpus_metadata",
            "guidance_source_status",
            "checklist_items",
            "checklist_statuses",
            "assessment_narrative_source",
            "review_warnings",
        )
        output = {k: state.get(k) for k in keys}
        output["status"] = state.get("status")
        return output
