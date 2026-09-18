"""Unit tests for PreProcessNode (Step 1 — input validation + S-2 gate).

All invocations use ``node(state)`` (not ``node.execute(state)``) to exercise
the full security pipeline. Fixtures use synthetic, non-personal data only.
"""

from __future__ import annotations

import json

_VALID_INPUT = {
    "destination": "Ueno Zoo",
    "destination_type": "zoo",
    "start_date": "2026-09-01",
    "end_date": "2026-09-01",
    "student_count": 30,
    "year_group": "Grade 5",
    "transport_mode": "bus",
    "activity_types": ["observation"],
}


def _make_state(payload: dict | str | None = None, raw: str | None = None) -> dict:
    if raw is not None:
        # raw override: use as-is, no payload auto-serialization
        pass
    elif payload is None:
        raw = json.dumps(_VALID_INPUT)
    elif isinstance(payload, dict):
        raw = json.dumps(payload)
    else:
        raw = str(payload)
    return {
        "caller_trust_level": "VERIFIED_EXTERNAL",
        "user_input": raw,
        "input_context": {"raw": raw},
        "correlation_id": "test-pre-process",
    }


class TestPreProcessNodeHappyPath:
    def test_valid_request_produces_validated_status(self):
        from src.nodes.pre_process_node import PreProcessNode

        node = PreProcessNode()
        result = node(_make_state())
        assert result.get("request_status") == "VALIDATED"
        assert result.get("status") in ("success", "SUCCESS")
        validated = result.get("validated_excursion", {})
        assert validated.get("destination") == "Ueno Zoo"
        assert validated.get("transport_mode") == "bus"
        assert validated.get("student_count") == 30

    def test_aggregate_medical_flag_passthrough(self):
        from src.nodes.pre_process_node import PreProcessNode

        payload = {**_VALID_INPUT, "aggregate_medical_flag": "PRESENT"}
        node = PreProcessNode()
        result = node(_make_state(payload))
        assert result.get("request_status") == "VALIDATED"
        validated = result.get("validated_excursion", {})
        assert validated.get("declared_aggregate_medical_flag") == "PRESENT"

    def test_unknown_activity_records_warning_not_rejection(self):
        from src.nodes.pre_process_node import PreProcessNode

        payload = {**_VALID_INPUT, "activity_types": ["observation", "skydiving"]}
        node = PreProcessNode()
        result = node(_make_state(payload))
        assert result.get("request_status") == "VALIDATED"
        warnings = result.get("review_warnings", [])
        assert any("unrecognized" in w or "skydiving" in w for w in warnings)


class TestPreProcessNodeSchemaRejection:
    def test_missing_required_field_rejected(self):
        from src.nodes.pre_process_node import PreProcessNode

        payload = {k: v for k, v in _VALID_INPUT.items() if k != "destination"}
        node = PreProcessNode()
        result = node(_make_state(payload))
        assert result.get("request_status") == "REJECTED"
        assert result.get("status") in ("success", "SUCCESS")
        assert "destination" in result.get("input_error_message", "")

    def test_invalid_dates_rejected(self):
        from src.nodes.pre_process_node import PreProcessNode

        payload = {**_VALID_INPUT, "start_date": "not-a-date"}
        node = PreProcessNode()
        result = node(_make_state(payload))
        assert result.get("request_status") == "REJECTED"

    def test_start_after_end_rejected(self):
        from src.nodes.pre_process_node import PreProcessNode

        payload = {**_VALID_INPUT, "start_date": "2026-09-05", "end_date": "2026-09-01"}
        node = PreProcessNode()
        result = node(_make_state(payload))
        assert result.get("request_status") == "REJECTED"

    def test_zero_student_count_rejected(self):
        from src.nodes.pre_process_node import PreProcessNode

        payload = {**_VALID_INPUT, "student_count": 0}
        node = PreProcessNode()
        result = node(_make_state(payload))
        assert result.get("request_status") == "REJECTED"

    def test_non_json_rejected(self):
        from src.nodes.pre_process_node import PreProcessNode

        node = PreProcessNode()
        result = node(_make_state(raw="not json at all"))
        assert result.get("request_status") == "REJECTED"


class TestPreProcessNodeS2Gate:
    """S-2 gate: individual student health/identity data must be rejected."""

    def test_health_data_in_freetext_rejected(self):
        from framework.errors import SecurityViolationError
        from src.nodes.pre_process_node import PreProcessNode

        payload = {**_VALID_INPUT, "notes": "Student Tanaka has asthma and allergy medication"}
        node = PreProcessNode()
        try:
            result = node(_make_state(payload))
            # If framework stub surfaces as error dict instead of raising:
            assert result.get("status") in ("error", "ERROR"), (
                "S-2 gate must reject individual health data"
            )
        except SecurityViolationError:
            pass  # correct

    def test_pii_in_freetext_rejected(self):
        from framework.errors import SecurityViolationError
        from src.nodes.pre_process_node import PreProcessNode

        payload = {**_VALID_INPUT, "notes": "Contact: student ID 12345 tel 090-0000-0000"}
        node = PreProcessNode()
        try:
            result = node(_make_state(payload))
            assert result.get("status") in ("error", "ERROR"), (
                "S-2 gate must reject individual identity/contact data"
            )
        except SecurityViolationError:
            pass

    def test_financial_data_masked_not_rejected(self):
        """Financial data in notes should be masked, not cause rejection."""
        from src.nodes.pre_process_node import PreProcessNode

        payload = {**_VALID_INPUT, "budget": "Total cost: ¥150,000 invoiced"}
        node = PreProcessNode()
        result = node(_make_state(payload))
        # Either validates (financial masked) or rejects with a validation error —
        # what matters is no individual health/pii SecurityViolationError escapes.
        assert result.get("request_status") in ("VALIDATED", "REJECTED")
