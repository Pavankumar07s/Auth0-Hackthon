"""Incident replay module for OpenClaw.

Reconstructs a full timeline of events around an incident,
including sensor data from a configurable window before and
after the incident. Used for post-incident analysis, caregiver
review, and medical record documentation.

Replay output:
    - Pre-incident context (5 min default)
    - Incident trigger event
    - Escalation timeline
    - Actions dispatched and results
    - Voice confirmation exchange
    - Resolution details
    - Sensor data streams throughout
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReplaySegment:
    """A single segment in the replay timeline."""

    timestamp: float
    source: str
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def relative_time(self) -> float:
        """Time relative to start of replay (set externally)."""
        return self.timestamp

    def to_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "timestamp": self.timestamp,
            "source": self.source,
            "event_type": self.event_type,
            "data": self.data,
        }


@dataclass
class IncidentReplay:
    """Complete replay of an incident."""

    incident_id: str
    created_at: float
    trigger_time: float
    segments: list[ReplaySegment] = field(default_factory=list)
    pre_window_seconds: float = 300.0
    post_window_seconds: float = 60.0

    def add_segment(
        self,
        source: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> None:
        """Add a segment to the replay."""
        self.segments.append(
            ReplaySegment(
                timestamp=timestamp or time.time(),
                source=source,
                event_type=event_type,
                data=data or {},
            )
        )

    @property
    def duration_seconds(self) -> float:
        """Total replay duration."""
        if not self.segments:
            return 0.0
        return self.segments[-1].timestamp - self.segments[0].timestamp

    def to_dict(self) -> dict[str, Any]:
        """Full serialized replay."""
        sorted_segs = sorted(self.segments, key=lambda s: s.timestamp)
        start = sorted_segs[0].timestamp if sorted_segs else self.trigger_time

        return {
            "incident_id": self.incident_id,
            "created_at": self.created_at,
            "trigger_time": self.trigger_time,
            "pre_window_seconds": self.pre_window_seconds,
            "post_window_seconds": self.post_window_seconds,
            "duration_seconds": self.duration_seconds,
            "segment_count": len(self.segments),
            "timeline": [
                {
                    **seg.to_dict(),
                    "relative_seconds": seg.timestamp - start,
                }
                for seg in sorted_segs
            ],
        }

    def to_summary(self) -> dict[str, Any]:
        """Brief summary for listings."""
        return {
            "incident_id": self.incident_id,
            "trigger_time": self.trigger_time,
            "duration_seconds": self.duration_seconds,
            "segment_count": len(self.segments),
        }


class ReplayBuilder:
    """Builds incident replays from aggregated context data.

    When an incident is created, the builder captures a snapshot
    of recent sensor data (pre-window) and continues recording
    until the incident resolves or the post-window expires.
    """

    def __init__(
        self,
        pre_window: float = 300.0,
        post_window: float = 60.0,
    ) -> None:
        self._pre_window = pre_window
        self._post_window = post_window
        self._active_replays: dict[str, IncidentReplay] = {}
        self._completed_replays: dict[str, IncidentReplay] = {}

    def start_replay(
        self,
        incident_id: str,
        trigger_time: float | None = None,
        pre_context: list[dict[str, Any]] | None = None,
    ) -> IncidentReplay:
        """Start recording an incident replay."""
        now = trigger_time or time.time()
        replay = IncidentReplay(
            incident_id=incident_id,
            created_at=time.time(),
            trigger_time=now,
            pre_window_seconds=self._pre_window,
            post_window_seconds=self._post_window,
        )

        # Add pre-incident context if available
        if pre_context:
            for item in pre_context:
                replay.add_segment(
                    source=item.get("source", "context"),
                    event_type=item.get("event_type", "sensor_data"),
                    data=item.get("data", {}),
                    timestamp=item.get("timestamp", now),
                )

        replay.add_segment(
            source="openclaw",
            event_type="replay_started",
            data={"incident_id": incident_id},
        )

        self._active_replays[incident_id] = replay
        logger.info("Replay started for incident %s", incident_id)
        return replay

    def add_event(
        self,
        incident_id: str,
        source: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Add an event to an active replay."""
        replay = self._active_replays.get(incident_id)
        if not replay:
            return
        replay.add_segment(source, event_type, data)

    def complete_replay(self, incident_id: str) -> IncidentReplay | None:
        """Mark a replay as complete and move to archive."""
        replay = self._active_replays.pop(incident_id, None)
        if not replay:
            return None

        replay.add_segment(
            source="openclaw",
            event_type="replay_completed",
        )

        self._completed_replays[incident_id] = replay
        logger.info(
            "Replay completed for incident %s (%d segments, %.0fs)",
            incident_id,
            len(replay.segments),
            replay.duration_seconds,
        )
        return replay

    def get_replay(self, incident_id: str) -> IncidentReplay | None:
        """Get a replay (active or completed)."""
        return (
            self._active_replays.get(incident_id)
            or self._completed_replays.get(incident_id)
        )

    def get_active_replays(self) -> list[str]:
        """Get IDs of active replays."""
        return list(self._active_replays.keys())

    def get_completed_replays(
        self, limit: int = 20
    ) -> list[IncidentReplay]:
        """Get most recent completed replays."""
        sorted_replays = sorted(
            self._completed_replays.values(),
            key=lambda r: r.created_at,
            reverse=True,
        )
        return sorted_replays[:limit]
