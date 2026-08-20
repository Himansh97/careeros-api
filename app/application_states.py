"""Canonical application states and the only normal transition policy."""
from __future__ import annotations

from enum import Enum


class ApplicationState(str, Enum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    TAILORING = "tailoring"
    DRAFT = "draft"
    READY = "ready"
    SUBMITTED = "submitted"
    RECRUITER_CONTACTED = "recruiter_contacted"
    SCREENING = "screening"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class InvalidApplicationState(ValueError):
    def __init__(self, value: object, *, legacy_write: bool = False) -> None:
        self.code = (
            "legacy_state_write" if legacy_write else "invalid_application_state"
        )
        super().__init__(f"{self.code}: {value!r}")


class InvalidApplicationTransition(ValueError):
    def __init__(self, current: object, target: object, *, code: str = "invalid_application_transition") -> None:
        self.code = code
        super().__init__(f"{code}: {current!r} -> {target!r}")


_LEGACY_READS = {
    "applied": ApplicationState.SUBMITTED,
    "interviewing": ApplicationState.INTERVIEW,
}

_NEXT = {
    ApplicationState.DISCOVERED: {ApplicationState.QUALIFIED},
    ApplicationState.QUALIFIED: {ApplicationState.TAILORING},
    ApplicationState.TAILORING: {ApplicationState.DRAFT},
    ApplicationState.DRAFT: {ApplicationState.READY},
    ApplicationState.READY: {ApplicationState.SUBMITTED},
    ApplicationState.SUBMITTED: {
        ApplicationState.RECRUITER_CONTACTED,
        ApplicationState.REJECTED,
        ApplicationState.WITHDRAWN,
    },
    ApplicationState.RECRUITER_CONTACTED: {
        ApplicationState.SCREENING,
        ApplicationState.REJECTED,
        ApplicationState.WITHDRAWN,
    },
    ApplicationState.SCREENING: {
        ApplicationState.INTERVIEW,
        ApplicationState.REJECTED,
        ApplicationState.WITHDRAWN,
    },
    ApplicationState.INTERVIEW: {
        ApplicationState.OFFER,
        ApplicationState.REJECTED,
        ApplicationState.WITHDRAWN,
    },
    ApplicationState.OFFER: set(),
    ApplicationState.REJECTED: set(),
    ApplicationState.WITHDRAWN: set(),
}


def normalize_legacy_state(value: str | ApplicationState) -> ApplicationState:
    """Normalize stored legacy spelling while rejecting unknown state text."""
    if isinstance(value, ApplicationState):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in _LEGACY_READS:
        return _LEGACY_READS[normalized]
    try:
        return ApplicationState(normalized)
    except ValueError as exc:
        raise InvalidApplicationState(value) from exc


def _write_state(value: str | ApplicationState) -> ApplicationState:
    if isinstance(value, ApplicationState):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in _LEGACY_READS:
        raise InvalidApplicationState(value, legacy_write=True)
    try:
        return ApplicationState(normalized)
    except ValueError as exc:
        raise InvalidApplicationState(value) from exc


def validate_transition(
    current: str | ApplicationState,
    target: str | ApplicationState,
    *,
    repair: bool = False,
    reason: str = "",
) -> ApplicationState:
    """Return a valid canonical target or raise a stable policy error."""
    current_state = normalize_legacy_state(current)
    target_state = _write_state(target)
    if current_state == target_state:
        return target_state
    if repair:
        if not reason.strip():
            raise InvalidApplicationTransition(
                current_state.value,
                target_state.value,
                code="repair_reason_required",
            )
        return target_state
    if target_state not in _NEXT[current_state]:
        raise InvalidApplicationTransition(current_state.value, target_state.value)
    return target_state
