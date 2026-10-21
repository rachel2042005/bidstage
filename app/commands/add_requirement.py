"""AddRequirement — FR-ENT-2.

Appends RequirementAdded on a draft the organizer owns. A published tender
rejects further requirements.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.commands.publish_tender import RequirementSpec, _requirement_payload
from app.domain.tender import (
    NotTenderOwnerError,
    TenderAlreadyPublishedError,
    TenderNotFoundError,
    replay_tender,
    tender_stream_id,
)
from app.events.event_types import REQUIREMENT_ADDED


@dataclass(frozen=True)
class AddRequirement:
    tender_id: str
    organizer_id: str
    requirement_type: str
    weight: int
    is_threshold: bool
    description: str


def add_requirement(store, tenders, command: AddRequirement) -> str:
    stream_id = tender_stream_id(command.tender_id)
    events = store.read_stream(stream_id)
    if not events:
        raise TenderNotFoundError(command.tender_id)
    state = replay_tender(events)
    if state.organizer_id != command.organizer_id:
        raise NotTenderOwnerError(command.tender_id)
    if state.published_at is not None:
        raise TenderAlreadyPublishedError("requirements can be added only to a draft")
    payload = _requirement_payload(
        RequirementSpec(
            requirement_type=command.requirement_type,
            weight=command.weight,
            is_threshold=command.is_threshold,
            description=command.description,
        )
    )
    expected = store.last_version(stream_id)
    store.append(
        stream_id,
        expected,
        REQUIREMENT_ADDED,
        payload,
        metadata={"actor": command.organizer_id, "actor_role": "organizer"},
    )
    tenders.apply_requirement(stream_id, payload)
    return payload["requirement_id"]
