"""Context aggregator for OpenClaw.

Collects and maintains a rolling window of sensor data, events,
and system state. When the policy engine needs to evaluate, the
aggregator builds a complete EscalationContext snapshot from the
latest data across all sources.

Data sources:
    - Vision-Agent events (MQTT)
    - SmartGuard anomaly scores (MQTT)
    - Health data from Noise smartwatch via HA (MQTT)
    - Fire / gas / door sensors (MQTT)
    - Voice confirmation responses (MQTT)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from src.policy_engine import EscalationContext

logger = logging.getLogger(__name__)


@dataclass
class SensorReading:
    """A timestamped sensor value."""

    timestamp: float
    source: str
    key: str
    value: Any

    @property
    def age(self) -> float:
        """Age in seconds."""
        return time.time() - self.timestamp


class ContextAggregator:
    """Aggregates multi-source data into EscalationContext snapshots.

    Maintains a configurable rolling window of readings per source
    to support context-aware policy decisions and incident replay.
    """

    def __init__(
        self,
        window_seconds: float = 120.0,
        max_readings_per_key: int = 200,
    ) -> None:
        self._window = window_seconds
        self._max_per_key = max_readings_per_key
        self._readings: dict[str, deque[SensorReading]] = {}
        self._latest: dict[str, Any] = {}
        self._lock = threading.Lock()

        # ── Tracked state ────────────────────────────────
        self._last_movement_time: float = time.time()
        self._fire_detected: bool = False
        self._gas_detected: bool = False
        self._fall_detected: bool = False
        self._wandering_detected: bool = False
        self._current_room: str = ""
        self._current_floor: int = 1
        self._person_id: str = ""
        self._voice_confirmation_active: bool = False
        self._voice_response: str | None = None

    # ── Ingest ───────────────────────────────────────────

    def ingest(self, source: str, key: str, value: Any) -> None:
        """Record a new sensor reading."""
        reading = SensorReading(
            timestamp=time.time(),
            source=source,
            key=key,
            value=value,
        )
        compound_key = f"{source}.{key}"
        with self._lock:
            if compound_key not in self._readings:
                self._readings[compound_key] = deque(
                    maxlen=self._max_per_key
                )
            self._readings[compound_key].append(reading)
            self._latest[compound_key] = value

    def ingest_vision_event(self, event: dict[str, Any]) -> None:
        """Process a vision-agent reasoned event."""
        event_type = event.get("event_type", "")
        severity = event.get("severity", "low")
        room = event.get("room", "")
        person_id = event.get("person_id", "")

        self.ingest("vision_agent", "event_type", event_type)
        self.ingest("vision_agent", "severity", severity)

        with self._lock:
            if room:
                self._current_room = room
            if person_id:
                self._person_id = person_id

            if event_type == "fall_detected":
                self._fall_detected = True
            elif event_type == "wandering_detected":
                self._wandering_detected = True
            elif event_type in ("movement_detected", "person_detected"):
                self._last_movement_time = time.time()

    def ingest_health(self, data: dict[str, Any]) -> None:
        """Process health data from smartwatch."""
        if "heart_rate" in data:
            self.ingest("health", "heart_rate", data["heart_rate"])
        if "spo2" in data:
            self.ingest("health", "spo2", data["spo2"])
        if "steps" in data:
            self.ingest("health", "steps", data["steps"])
        if "stress" in data:
            self.ingest("health", "stress", data["stress"])

    def ingest_smartguard(self, data: dict[str, Any]) -> None:
        """Process SmartGuard anomaly data."""
        if "anomaly_score" in data:
            self.ingest(
                "smartguard", "anomaly_score", data["anomaly_score"]
            )
        if "is_anomaly" in data:
            self.ingest(
                "smartguard", "is_anomaly", data["is_anomaly"]
            )

    def ingest_environmental(self, sensor: str, value: Any) -> None:
        """Process fire/gas/door sensor data."""
        self.ingest("environment", sensor, value)
        with self._lock:
            if sensor == "fire" and value:
                self._fire_detected = True
            elif sensor == "gas" and value:
                self._gas_detected = True

    def ingest_voice_response(self, response: str) -> None:
        """Process Alexa voice confirmation response."""
        with self._lock:
            self._voice_response = response
            self._voice_confirmation_active = True
        self.ingest("voice", "response", response)

    def clear_fall(self) -> None:
        """Clear fall detected flag after resolution."""
        with self._lock:
            self._fall_detected = False

    def clear_environmental(self, sensor: str) -> None:
        """Clear environmental flag."""
        with self._lock:
            if sensor == "fire":
                self._fire_detected = False
            elif sensor == "gas":
                self._gas_detected = False

    def clear_wandering(self) -> None:
        """Clear wandering flag."""
        with self._lock:
            self._wandering_detected = False

    def clear_voice_state(self) -> None:
        """Reset voice confirmation state."""
        with self._lock:
            self._voice_confirmation_active = False
            self._voice_response = None

    # ── Build Context ────────────────────────────────────

    def build_context(self) -> EscalationContext:
        """Build a complete EscalationContext from current state."""
        with self._lock:
            now = time.time()
            inactivity = now - self._last_movement_time
            has_movement = inactivity < 30.0

            return EscalationContext(
                fire_detected=self._fire_detected,
                gas_leak_detected=self._gas_detected,
                fall_detected=self._fall_detected,
                heart_rate=self._get_latest_float(
                    "health.heart_rate"
                ),
                spo2=self._get_latest_float("health.spo2"),
                inactivity_seconds=inactivity,
                movement_present=has_movement,
                anomaly_score=self._get_latest_float(
                    "smartguard.anomaly_score", 0.0
                ),
                wandering_detected=self._wandering_detected,
                vision_agent_severity=self._get_latest_str(
                    "vision_agent.severity", "info"
                ),
                room=self._current_room,
                floor=self._current_floor,
                person_id=self._person_id,
                voice_confirmation_pending=self._voice_confirmation_active,
                voice_response=self._voice_response,
            )

    # ── Query helpers ────────────────────────────────────

    def get_latest(self, source: str, key: str) -> Any | None:
        """Get the most recent value for a source.key."""
        return self._latest.get(f"{source}.{key}")

    def get_history(
        self, source: str, key: str, seconds: float | None = None
    ) -> list[SensorReading]:
        """Get reading history within time window."""
        compound = f"{source}.{key}"
        window = seconds or self._window
        cutoff = time.time() - window
        readings = self._readings.get(compound, deque())
        return [r for r in readings if r.timestamp >= cutoff]

    def get_heart_rate_trend(
        self, window_seconds: float = 60.0
    ) -> dict[str, float | None]:
        """Get heart rate trend (min, max, avg, latest)."""
        readings = self.get_history("health", "heart_rate", window_seconds)
        if not readings:
            return {
                "min": None,
                "max": None,
                "avg": None,
                "latest": None,
                "count": 0,
            }
        values = [r.value for r in readings if isinstance(r.value, (int, float))]
        if not values:
            return {
                "min": None,
                "max": None,
                "avg": None,
                "latest": None,
                "count": 0,
            }
        return {
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "latest": values[-1],
            "count": len(values),
        }

    def get_location_info(self) -> dict[str, Any]:
        """Get current location info for Telegram queries."""
        with self._lock:
            return {
                "room": self._current_room or "unknown",
                "floor": self._current_floor,
                "person_id": self._person_id,
                "last_movement_age": (
                    time.time() - self._last_movement_time
                ),
            }

    def get_snapshot(self) -> dict[str, Any]:
        """Full snapshot for diagnostics / telemetry."""
        with self._lock:
            return {
                "latest_values": dict(self._latest),
                "fire_detected": self._fire_detected,
                "gas_detected": self._gas_detected,
                "fall_detected": self._fall_detected,
                "wandering_detected": self._wandering_detected,
                "current_room": self._current_room,
                "current_floor": self._current_floor,
                "person_id": self._person_id,
                "inactivity_seconds": (
                    time.time() - self._last_movement_time
                ),
                "voice_state": {
                    "active": self._voice_confirmation_active,
                    "response": self._voice_response,
                },
                "reading_counts": {
                    k: len(v)
                    for k, v in self._readings.items()
                },
            }

    # ── Private helpers ──────────────────────────────────

    def _get_latest_float(
        self, compound_key: str, default: float | None = None
    ) -> float | None:
        """Get latest value as float."""
        val = self._latest.get(compound_key)
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def _get_latest_str(
        self, compound_key: str, default: str = ""
    ) -> str:
        """Get latest value as string."""
        val = self._latest.get(compound_key)
        return str(val) if val is not None else default
