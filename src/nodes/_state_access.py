"""_state_access — shared state-field reader for the inner domain nodes.

The inner BaseGraph is invoked by ``DomainWorkflowGraphNode`` with a structured
payload as ``user_input``. Fields threaded across inner nodes land as top-level
state keys; fields seeded from the outer boundary arrive inside the ``user_input``
payload. ``get_field`` reads the top-level key first and transparently falls back
to the payload, so inner nodes work identically under the real framework and the
test stubs. This module defines no node classes.
"""

from __future__ import annotations

from typing import Any


def get_field(state: dict[str, Any], key: str, default: Any = None) -> Any:
    """Return ``state[key]`` if meaningfully set, else fall back to the payload."""
    if key in state and state[key] not in (None, "", [], {}):
        return state[key]
    payload = state.get("user_input")
    if isinstance(payload, dict) and key in payload and payload[key] not in (None, "", [], {}):
        return payload[key]
    return default
