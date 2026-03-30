"""Incident state machine for OpenClaw.

Manages the lifecycle of safety incidents from detection through
resolution. Every incident is tracked with a unique ID, full
timeline, escalation history, and final outcome.

States:
    DETECTED  → Initial detection
    ASSESSING → Gathering context, running policy
    ESCALATED → Actions dispatched (WARNING/HIGH_RISK/CRITICAL)
    VOICE_PENDING → Awaiting voice confirmation (Alexa)
    RESOLVED  → Incident handled or downgraded
    EXPIRED   → Incident timed out without resolution
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.policy_engine import EscalationLevel, PolicyDecision

logger = logging.getLogger(__name__)


class IncidentState(Enum):
    """State machine states for an incident."""

    DETECTED = "detected"
    ASSESSING = "assessing"
    ESCALATED = "escalated"
    VOICE_PENDING = "voice_pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"


@dataclass
class TimelineEntry:
    """Single entry in an incident timeline."""

    timestamp: float
    event: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "timestamp": self.timestamp,
            "event": self.event,
            "details": self.details,
        }


@dataclass
class Incident:
    """A tracked safety incident."""

    id: str
    created_at: float
    trigger_event: str
    trigger_source: str
    state: IncidentState = IncidentState.DETECTED
    level: EscalationLevel = EscalationLevel.MONITOR
    person_id: str = ""
    room: str = ""
    floor: int = 1
    timeline: list[TimelineEntry] = field(default_factory=list)
    actions_dispatched: list[str] = field(default_factory=list)
    voice_response: str | None = None
    resolved_at: float | None = None
    resolution: str = ""
    escalation_count: int = 0

    def add_event(self, event: str, details: dict[str, Any] | None = None) -> None:
        """Add a timeline entry."""
        self.timeline.append(
            TimelineEntry(
                timestamp=time.time(),
                event=event,
                details=details or {},
            )
        )

    @property
    def duration_seconds(self) -> float:
        """Seconds since incident creation."""
        end = self.resolved_at or time.time()
        return end - self.created_at

    @property
    def is_active(self) -> bool:
        """Whether the incident is still active."""
        return self.state not in (IncidentState.RESOLVED, IncidentState.EXPIRED)

    def to_dict(self) -> dict[str, Any]:
        """Full serialization for REST API / MQTT."""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "trigger_event": self.trigger_event,
            "trigger_source": self.trigger_source,
            "state": self.state.value,
            "level": self.level.value,
            "level_name": self.level.name,
            "person_id": self.person_id,
            "room": self.room,
            "floor": self.floor,
            "timeline": [e.to_dict() for e in self.timeline],
            "actions_dispatched": self.actions_dispatched,
            "voice_response": self.voice_response,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
            "duration_seconds": self.duration_seconds,
            "escalation_count": self.escalation_count,
        }

    def to_summary(self) -> dict[str, Any]:
        """Brief summary for listings."""
        return {
            "id": self.id,
            "state": self.state.value,
            "level_name": self.level.name,
            "trigger_event": self.trigger_event,
            "room": self.room,
            "duration_seconds": self.duration_seconds,
            "escalation_count": self.escalation_count,
        }


class IncidentManager:
    """Manages incident lifecycle and provides query capabilities."""

    def __init__(
        self,
        max_active: int = 50,
        auto_expire_seconds: float = 3600,
        dedup_window: float = 60.0,
    ) -> None:
        self._incidents: dict[str, Incident] = {}
        self._lock = threading.Lock()
        self._max_active = max_active
        self._auto_expire = auto_expire_seconds
        self._dedup_window = dedup_window
        self._last_triggers: dict[str, float] = {}

        # Counters
        self.total_incidents = 0
        self.total_escalations = 0

    def create_incident(
        self,
        trigger_event: str,
        trigger_source: str,
        person_id: str = "",
        room: str = "",
        floor: int = 1,
    ) -> Incident | None:
        """Create a new incident if not a duplicate.

        Returns None if a similar incident was created within the
        dedup window.
        """
        # Dedup check
        dedup_key = f"{trigger_event}:{person_id}"
        now = time.time()
        with self._lock:
            last = self._last_triggers.get(dedup_key, 0.0)
            if now - last < self._dedup_window:
                logger.debug(
                    "Suppressed duplicate incident: %s (%.0fs ago)",
                    dedup_key,
                    now - last,
                )
                return None

            self._last_triggers[dedup_key] = now

            incident = Incident(
                id=str(uuid.uuid4())[:8],
                created_at=now,
                trigger_event=trigger_event,
                trigger_source=trigger_source,
                person_id=person_id,
                room=room,
                floor=floor,
            )
            incident.add_event(
                "incident_created",
                {
                    "trigger": trigger_event,
                    "source": trigger_source,
                    "person_id": person_id,
                },
            )

            self._incidents[incident.id] = incident
            self.total_incidents += 1

            logger.info(
                "Incident created: %s trigger=%s person=%s room=%s",
                incident.id,
                trigger_event,
                person_id,
                room,
            )
            return incident

    def escalate(
        self, incident_id: str, decision: PolicyDecision
    ) -> Incident | None:
        """Apply a policy decision to escalate an incident."""
        with self._lock:
            incident = self._incidents.get(incident_id)
            if not incident or not incident.is_active:
                return None

            old_level = incident.level
            incident.level = decision.level
            incident.state = IncidentState.ESCALATED
            incident.actions_dispatched.extend(decision.actions)
            incident.escalation_count += 1
            self.total_escalations += 1

            incident.add_event(
                "escalated",
                {
                    "from_level": old_level.name,
                    "to_level": decision.level.name,
                    "reasons": decision.reasons,
                    "actions": decision.actions,
                },
            )

            if decision.requires_voice_confirmation:
                incident.state = IncidentState.VOICE_PENDING
                incident.add_event("voice_confirmation_requested")

            logger.info(
                "Incident %s escalated: %s → %s reasons=%s",
                incident_id,
                old_level.name,
                decision.level.name,
                decision.reasons,
            )
            return incident

    def set_voice_response(
        self, incident_id: str, response: str | None
    ) -> Incident | None:
        """Record voice confirmation response for an incident."""
        with self._lock:
            incident = self._incidents.get(incident_id)
            if not incident:
                return None
            incident.voice_response = response
            incident.add_event(
                "voice_response_received",
                {"response": response},
            )
            return incident

    def resolve(
        self, incident_id: str, resolution: str = "resolved"
    ) -> Incident | None:
        """Mark an incident as resolved."""
        with self._lock:
            incident = self._incidents.get(incident_id)
            if not incident:
                return None

            incident.state = IncidentState.RESOLVED
            incident.resolved_at = time.time()
            incident.resolution = resolution
            incident.add_event(
                "resolved", {"resolution": resolution}
            )

            logger.info(
                "Incident %s resolved: %s (%.0fs)",
                incident_id,
                resolution,
                incident.duration_seconds,
            )
            return incident

    def get_incident(self, incident_id: str) -> Incident | None:
        """Get a specific incident."""
        return self._incidents.get(incident_id)

    def get_active_incidents(self) -> list[Incident]:
        """Get all active incidents."""
        self._expire_stale()
        return [
            inc for inc in self._incidents.values() if inc.is_active
        ]

    def get_voice_pending(self) -> list[Incident]:
        """Get incidents awaiting voice confirmation."""
        return [
            inc
            for inc in self._incidents.values()
            if inc.state == IncidentState.VOICE_PENDING
        ]

    def get_recent(self, limit: int = 20) -> list[Incident]:
        """Get most recent incidents."""
        sorted_incs = sorted(
            self._incidents.values(),
            key=lambda i: i.created_at,
            reverse=True,
        )
        return sorted_incs[:limit]

    def _expire_stale(self) -> None:
        """Auto-expire old active incidents."""
        now = time.time()
        with self._lock:
            for inc in self._incidents.values():
                if (
                    inc.is_active
                    and now - inc.created_at > self._auto_expire
                ):
                    inc.state = IncidentState.EXPIRED
                    inc.resolved_at = now
                    inc.resolution = "auto_expired"
                    inc.add_event("auto_expired")

    # ── Query helpers for PicoClaw / Telegram ────────────────

    @property
    def stats(self) -> dict[str, Any]:
        """Current incident statistics."""
        active = self.get_active_incidents()
        return {
            "total_incidents": self.total_incidents,
            "total_escalations": self.total_escalations,
            "active_count": len(active),
            "highest_active_level": (
                max(i.level for i in active).name if active else "NONE"
            ),
        }
