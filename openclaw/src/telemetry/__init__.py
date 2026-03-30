"""Telemetry module for OpenClaw.

Provides live telemetry streaming during active incidents.
When an incident reaches HIGH_RISK or CRITICAL, telemetry
starts publishing real-time vitals and location data at a
configurable interval.

This data can be consumed by:
    - Ambulance / emergency responders (via REST / MQTT)
    - Caregiver Telegram dashboard
    - HA dashboard live cards
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class TelemetryStream:
    """Manages live telemetry streaming for an incident."""

    def __init__(
        self,
        incident_id: str,
        interval: float = 5.0,
        data_fn: Callable[[], dict[str, Any]] | None = None,
        publish_fn: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._incident_id = incident_id
        self._interval = interval
        self._data_fn = data_fn
        self._publish_fn = publish_fn
        self._running = False
        self._thread: threading.Thread | None = None
        self._started_at: float = 0
        self._sample_count = 0

    def start(self) -> None:
        """Start streaming telemetry."""
        if self._running:
            return
        self._running = True
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._stream_loop,
            daemon=True,
            name=f"telemetry-{self._incident_id}",
        )
        self._thread.start()
        logger.info(
            "Telemetry stream started for incident %s (every %.0fs)",
            self._incident_id,
            self._interval,
        )

    def stop(self) -> None:
        """Stop streaming telemetry."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._interval + 1)
        logger.info(
            "Telemetry stream stopped for incident %s (%d samples)",
            self._incident_id,
            self._sample_count,
        )

    @property
    def is_running(self) -> bool:
        """Whether the stream is currently active."""
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        """Stream statistics."""
        return {
            "incident_id": self._incident_id,
            "running": self._running,
            "started_at": self._started_at,
            "sample_count": self._sample_count,
            "duration_seconds": (
                time.time() - self._started_at
                if self._started_at
                else 0
            ),
        }

    def _stream_loop(self) -> None:
        """Background loop that publishes telemetry at interval."""
        while self._running:
            try:
                data = self._collect_sample()
                if self._publish_fn:
                    self._publish_fn(data)
                self._sample_count += 1
            except Exception:
                logger.exception(
                    "Error in telemetry stream for %s",
                    self._incident_id,
                )
            time.sleep(self._interval)

    def _collect_sample(self) -> dict[str, Any]:
        """Collect a single telemetry sample."""
        base = {
            "incident_id": self._incident_id,
            "timestamp": time.time(),
            "sample_number": self._sample_count + 1,
        }
        if self._data_fn:
            snapshot = self._data_fn()
            base.update(snapshot)
        return base


class TelemetryManager:
    """Manages telemetry streams for multiple concurrent incidents."""

    def __init__(
        self,
        default_interval: float = 5.0,
        data_fn: Callable[[], dict[str, Any]] | None = None,
        publish_fn: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._interval = default_interval
        self._data_fn = data_fn
        self._publish_fn = publish_fn
        self._streams: dict[str, TelemetryStream] = {}

    def start_stream(self, incident_id: str) -> TelemetryStream:
        """Start or get a telemetry stream for an incident."""
        if incident_id in self._streams:
            stream = self._streams[incident_id]
            if stream.is_running:
                return stream

        stream = TelemetryStream(
            incident_id=incident_id,
            interval=self._interval,
            data_fn=self._data_fn,
            publish_fn=self._publish_fn,
        )
        self._streams[incident_id] = stream
        stream.start()
        return stream

    def stop_stream(self, incident_id: str) -> None:
        """Stop a specific telemetry stream."""
        stream = self._streams.get(incident_id)
        if stream:
            stream.stop()

    def stop_all(self) -> None:
        """Stop all active telemetry streams."""
        for stream in self._streams.values():
            stream.stop()

    def get_active_streams(self) -> list[dict[str, Any]]:
        """Get stats for all active streams."""
        return [
            s.stats
            for s in self._streams.values()
            if s.is_running
        ]
