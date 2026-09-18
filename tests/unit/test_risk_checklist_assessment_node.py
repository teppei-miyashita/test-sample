"""Unit tests for RiskChecklistAssessmentNode (Step 5 — checklist assessment).

Tests the safety-first health default (incomplete completeness → REQUIRES_REVIEW),
LLM-narrative fallback, and deterministic status logic.
Invocations use ``node(state)``; no individual student data in fixtures.
"""

from __future__ import annotations

def _make_state(health_completeness="COMPLETE", health_flag="NONE",
                coverage="FULL", citations=None) -> dict:
    if citations is None:
        citations = [
            {"category": "health_medical", "source": "MEXT-2025", "section": "§3",
             "is_fallback": False},
            {"category": "emergency_protocol", "source": "MEXT-2025", "section": "§5",
             "is_fallback": False},
        ]
    return {
        "caller_trust_level": "ANONYMOUS",
        "session_id": "test-session",
        "thread_id": "test-thread",
        "trace_id": "test-trace",
        "correlation_id": "test-checklist",
        "validated_excursion": {
            "destination_type": "zoo",
            "transport_mode": "bus",
            "activity_types": ["observation"],
        },
        "risk_category_map": {
            "health_medical": {"applicable": True, "drivers": ["baseline"], "precedence": 0},
            "emergency_protocol": {"applicable": True, "drivers": ["baseline"], "precedence": 1},
            "transport_safety": {"applicable": True, "drivers": ["transport:bus"], "precedence": 2},
            "weather_outdoor": {"applicable": False, "drivers": [], "precedence": 3},
            "venue_capacity": {"applicable": False, "drivers": [], "precedence": 4},
            "supervision_ratio": {"applicable": True, "drivers": ["baseline"], "precedence": 5},
            "hygiene": {"applicable": False, "drivers": [], "precedence": 6},
            "insurance": {"applicable": True, "drivers": ["baseline"], "precedence": 7},
        },
        "health_record_completeness": health_completeness,
        "aggregate_health_risk_flag": health_flag,
        "destination_coverage": coverage,
        "guidance_citations": citations,
        "review_warnings": [],
    }


class TestRiskChecklistHealthDefault:
    def test_health_complete_and_no_flag_gives_complete(self):
        """When completeness is COMPLETE and no health risk present, health items may be COMPLETE.

        evidence_required controls whether external evidence is needed, but the
        _decide_status health branch short-circuits before checking evidence_required —
        COMPLETE+NONE health state returns COMPLETE for all health items (by design).
        """
        from src.nodes.risk_checklist_assessment_node import RiskChecklistAssessmentNode

        node = RiskChecklistAssessmentNode()
        result = node(_make_state(health_completeness="COMPLETE", health_flag="NONE"))
        statuses = result.get("checklist_statuses", {})
        # COMPLETE+NONE is the only case health items can be COMPLETE.
        assert statuses.get("hm-1") == "COMPLETE"
        assert statuses.get("hm-2") == "COMPLETE"

    def test_health_unknown_completeness_forces_requires_review(self):
        from src.nodes.risk_checklist_assessment_node import RiskChecklistAssessmentNode

        node = RiskChecklistAssessmentNode()
        result = node(_make_state(health_completeness="UNKNOWN"))
        statuses = result.get("checklist_statuses", {})
        assert statuses.get("hm-1") == "REQUIRES_REVIEW"
        assert statuses.get("hm-2") == "REQUIRES_REVIEW"

    def test_health_incomplete_completeness_forces_requires_review(self):
        from src.nodes.risk_checklist_assessment_node import RiskChecklistAssessmentNode

        node = RiskChecklistAssessmentNode()
        result = node(_make_state(health_completeness="INCOMPLETE"))
        statuses = result.get("checklist_statuses", {})
        assert statuses.get("hm-1") == "REQUIRES_REVIEW"
        assert statuses.get("hm-2") == "REQUIRES_REVIEW"

    def test_health_present_flag_forces_requires_review(self):
        from src.nodes.risk_checklist_assessment_node import RiskChecklistAssessmentNode

        node = RiskChecklistAssessmentNode()
        result = node(_make_state(health_completeness="COMPLETE", health_flag="PRESENT"))
        statuses = result.get("checklist_statuses", {})
        assert statuses.get("hm-1") == "REQUIRES_REVIEW"
        assert statuses.get("hm-2") == "REQUIRES_REVIEW"


class TestRiskChecklistCoverageDefault:
    def test_not_found_coverage_makes_non_evidence_items_requires_review(self):
        from src.nodes.risk_checklist_assessment_node import RiskChecklistAssessmentNode

        node = RiskChecklistAssessmentNode()
        result = node(_make_state(coverage="NOT_FOUND", citations=[
            {"category": "health_medical", "source": "MEXT-2025", "section": "§3",
             "is_fallback": True},
        ]))
        statuses = result.get("checklist_statuses", {})
        # Without real (non-fallback) coverage, non-evidence transport items → REQUIRES_REVIEW
        assert statuses.get("ts-1") == "REQUIRES_REVIEW"

    def test_inactive_category_gives_not_applicable(self):
        from src.nodes.risk_checklist_assessment_node import RiskChecklistAssessmentNode

        node = RiskChecklistAssessmentNode()
        result = node(_make_state())
        statuses = result.get("checklist_statuses", {})
        # weather_outdoor is inactive in our fixture
        assert statuses.get("wo-1") == "NOT_APPLICABLE"

    def test_requires_review_warning_added(self):
        from src.nodes.risk_checklist_assessment_node import RiskChecklistAssessmentNode

        node = RiskChecklistAssessmentNode()
        result = node(_make_state(health_completeness="UNKNOWN"))
        warnings = result.get("review_warnings", [])
        assert any("REQUIRES_REVIEW" in w for w in warnings)


class TestRiskChecklistNoApproval:
    def test_assessment_never_makes_approval_decision(self):
        from src.nodes.risk_checklist_assessment_node import RiskChecklistAssessmentNode

        node = RiskChecklistAssessmentNode()
        result = node(_make_state())
        items = result.get("checklist_items", [])
        for item in items:
            status = item.get("status", "")
            assert status not in ("APPROVED", "DENIED", "REJECTED"), (
                f"checklist item status must not be an approval decision; got {status!r}"
            )
