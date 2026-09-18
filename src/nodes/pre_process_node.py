"""PreProcessNode — Step 1 input schema validation and S-2 input security gate.

Boundary node (outer AgentBaseGraph, VERIFIED_EXTERNAL). Validates the excursion
request schema, rejects any individual-level student health/identity data in free
text (S-2), and masks school-internal financial data before it can reach state.
Only sanitized aggregate values proceed to the linear domain pipeline.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, ClassVar, Dict, Tuple

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import pii_guard
from src.services.runtime_config import load_config

_REQUIRED_FIELDS = (
    "destination",
    "destination_type",
    "start_date",
    "end_date",
    "student_count",
    "year_group",
    "transport_mode",
    "activity_types",
)


class PreProcessNode(FunctionNode):
    """Validate the excursion request and enforce the input data boundary."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    # Structured, separately-validated fields — excluded from free-text PII scan so
    # ISO dates and counts never false-match identity/contact patterns.
    _NON_FREETEXT_KEYS = frozenset({"start_date", "end_date", "student_count"})

    # ── S-2 input gate (framework calls this via __call__) ───────────────
    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """Reject individual student health/identity data in any free-text field.

        The default framework gate PII-masks standard fields; this domain hook adds
        a hard rejection for individual health/consent/identity signals — those must
        never enter the pipeline, masked or not. Scans free-text field values
        (including nested), excluding the structured date/count fields.
        """
        scan_target = self._freetext_scope(state)
        findings = pii_guard.scan_prohibited(scan_target)
        health = [f.split(":", 1)[1] for f in findings if f.startswith("health:")]
        pii = [f.split(":", 1)[1] for f in findings if f.startswith("pii:")]
        if health:
            raise SecurityViolationError(
                "individual student health/consent data is prohibited in the request "
                f"(signals: {', '.join(sorted(set(health)))}); submit only aggregate flags"
            )
        if pii:
            raise SecurityViolationError(
                "individual student identity/contact data is prohibited in the request "
                f"(signals: {', '.join(sorted(set(pii)))})"
            )
        return state

    def _freetext_scope(self, state: dict[str, Any]) -> Any:
        """Return the value(s) to scan: parsed free-text fields when the request is
        JSON, else the raw string."""
        raw = self._raw_input(state)
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return raw
        if isinstance(parsed, dict):
            return {k: v for k, v in parsed.items() if k not in self._NON_FREETEXT_KEYS}
        return raw

    @staticmethod
    def _raw_input(state: dict[str, Any]) -> str:
        ctx_in = state.get("input_context") or {}
        raw = ctx_in.get("raw")
        if raw is None:
            raw = state.get("user_input", "")
        if isinstance(raw, (dict, list)):
            return json.dumps(raw, ensure_ascii=False)
        return raw if isinstance(raw, str) else str(raw)

    # ── execute (Step 1 logic) ───────────────────────────────────────────
    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        config = load_config()
        raw = self._raw_input(state)

        if len(raw.encode("utf-8")) > config["max_request_bytes"]:
            return self._reject(state, ["request exceeds maximum size"])

        request, parse_err = self._parse(state, raw)
        if parse_err:
            return self._reject(state, [parse_err])

        # Mask school-internal financial data before anything is stored.
        masked_fields: list[str] = []
        for key in ("notes", "budget", "cost", "payment"):
            if isinstance(request.get(key), str):
                _masked, hit = pii_guard.mask_financial(request[key])
                if hit:
                    request[key] = _masked
                    masked_fields.extend(f"{key}:{h}" for h in hit)

        errors, validated, unknown_activities = self._validate(request, config)
        if errors:
            return self._reject(state, errors, masked_fields)

        warnings = []
        if unknown_activities:
            warnings.append(
                f"unrecognized activity types recorded for fallback handling: " f"{', '.join(unknown_activities)}"
            )

        emit_trace_event(
            "PreProcessNode_input_validated",
            {
                "destination_type": validated["destination_type"],
                "transport_mode": validated["transport_mode"],
                "activity_count": len(validated["activity_types"]),
                "masked_field_count": len(masked_fields),
            },
            state,
        )
        return {
            "validated_excursion": validated,
            "masked_fields": masked_fields,
            "validation_errors": [],
            "request_status": "VALIDATED",
            "review_warnings": warnings,
            "requires_human_review": True,
            "status": AgentStatus.SUCCESS.value,
        }

    # ── helpers ──────────────────────────────────────────────────────────
    def _parse(self, state: dict[str, Any], raw: str) -> Tuple[Dict[str, Any], str]:
        ctx_in = state.get("input_context") or {}
        structured = ctx_in.get("structured")
        if isinstance(structured, dict):
            return dict(structured), ""
        if not raw or not raw.strip():
            return {}, "empty request: no excursion details provided"
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}, "request is not valid JSON excursion schema"
        if not isinstance(parsed, dict):
            return {}, "request must be a JSON object of excursion fields"
        return parsed, ""

    def _validate(self, request: Dict[str, Any], config: dict[str, Any]) -> Tuple[list[str], Dict[str, Any], list[str]]:
        errors: list[str] = []
        for field in _REQUIRED_FIELDS:
            if field not in request or request[field] in (None, "", []):
                errors.append(f"missing required field: {field}")
        if errors:
            return errors, {}, []

        destination = str(request["destination"]).strip()
        if not destination:
            errors.append("destination must not be empty")

        start, end = str(request["start_date"]), str(request["end_date"])
        if not (self._is_iso_date(start) and self._is_iso_date(end)):
            errors.append("start_date/end_date must be ISO dates (YYYY-MM-DD)")
        elif start > end:
            errors.append("start_date must not be after end_date")

        try:
            count = int(request["student_count"])
            if count <= 0:
                errors.append("student_count must be a positive integer")
            elif count > config["max_student_count"]:
                errors.append(f"student_count exceeds maximum {config['max_student_count']}")
        except (ValueError, TypeError):
            count = 0
            errors.append("student_count must be an integer")

        transport = str(request["transport_mode"]).strip().lower()
        if transport not in config["allowed_transport_modes"]:
            errors.append(f"invalid transport_mode: {transport}")

        activities_in = request["activity_types"]
        if not isinstance(activities_in, list):
            errors.append("activity_types must be a list")
            activities = []
        else:
            activities = [str(a).strip().lower() for a in activities_in if str(a).strip()]
            if not activities:
                errors.append("activity_types must not be empty")

        unknown_activities = [a for a in activities if a not in config["allowed_activity_types"]]

        if errors:
            return errors, {}, []

        # aggregate_medical_flag is a controlled boolean/enum only — never free text.
        declared_flag = str(request.get("aggregate_medical_flag", "UNKNOWN")).upper()
        if declared_flag not in ("NONE", "PRESENT", "UNKNOWN"):
            declared_flag = "UNKNOWN"

        validated = {
            "destination": destination,
            "destination_type": str(request["destination_type"]).strip().lower(),
            "start_date": start,
            "end_date": end,
            "student_count": count,
            "year_group": str(request["year_group"]).strip(),
            "transport_mode": transport,
            "activity_types": activities,
            "declared_aggregate_medical_flag": declared_flag,
        }
        return [], validated, unknown_activities

    @staticmethod
    def _is_iso_date(value: str) -> bool:
        try:
            date.fromisoformat(value)
            return True
        except (ValueError, TypeError):
            return False

    def _reject(
        self,
        state: dict[str, Any],
        errors: list[str],
        masked_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        emit_trace_event(
            "PreProcessNode_input_rejected",
            {"error_count": len(errors), "codes": errors[:5]},
            state,
        )
        return {
            "validated_excursion": {},
            "masked_fields": masked_fields or [],
            "validation_errors": errors,
            "request_status": "REJECTED",
            "review_warnings": ["request rejected at input validation — see validation_errors"],
            "requires_human_review": True,
            "status": AgentStatus.SUCCESS.value,
            "input_error_message": "The excursion request failed validation: " + "; ".join(errors),
            "input_error_guidance": [
                "Provide a JSON object with destination, destination_type, start_date, end_date, student_count, year_group, transport_mode, and activity_types.",
                "Use YYYY-MM-DD dates and provide only aggregate, non-personal student information.",
            ],
        }


# Domain-facing alias — the input-validation role of this boundary node (issue #3).
InputValidationNode = PreProcessNode
