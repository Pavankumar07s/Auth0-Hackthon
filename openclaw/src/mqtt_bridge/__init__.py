"""MQTT bridge for OpenClaw.

Handles all MQTT connectivity — subscribes to upstream event
topics, parses payloads, feeds the context aggregator, and
publishes actions, telemetry, and status updates.

Topic Map (subscribe):
    vision_agent/reasoned_event   → Vision-Agent decisions
    vision/events                 → Raw vision events
    smartguard/anomaly            → SmartGuard anomaly scores
    homeassistant/sensor/+/state  → Health / environment sensors
    etms/voice/response           → Alexa voice confirmation
    etms/openclaw/command         → External commands (REST relay)

Topic Map (publish):
    etms/openclaw/incident        → Incident lifecycle events
    etms/openclaw/action          → Dispatched actions
    etms/openclaw/telemetry       → Live telemetry stream
    etms/openclaw/status          → Service health / heartbeat
    etms/openclaw/daily_report    → Daily AI summary
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MQTTBridge:
    """MQTT connection manager for OpenClaw."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        client_id: str = "openclaw",
        publish_prefix: str = "etms/openclaw",
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._publish_prefix = publish_prefix

        self._client = mqtt.Client(
            client_id=client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        if username and password:
            self._client.username_pw_set(username, password)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._subscriptions: dict[str, list[Callable]] = {}
        self._connected = False
        self._reconnect_count = 0

    # ── Connection lifecycle ─────────────────────────────

    def connect(self) -> None:
        """Connect to MQTT broker."""
        logger.info(
            "Connecting to MQTT broker at %s:%d", self._host, self._port
        )
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()

    def disconnect(self) -> None:
        """Gracefully disconnect."""
        self._publish_status("offline")
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False
        logger.info("MQTT bridge disconnected")

    @property
    def is_connected(self) -> bool:
        """Whether the MQTT client is connected."""
        return self._connected

    # ── Subscription ─────────────────────────────────────

    def subscribe(self, topic: str, callback: Callable) -> None:
        """Register a callback for a topic pattern."""
        if topic not in self._subscriptions:
            self._subscriptions[topic] = []
        self._subscriptions[topic].append(callback)

        if self._connected:
            self._client.subscribe(topic, qos=1)

    # ── Publishing ───────────────────────────────────────

    def publish_incident(self, incident_data: dict[str, Any]) -> None:
        """Publish incident lifecycle event."""
        self._publish(
            f"{self._publish_prefix}/incident",
            incident_data,
        )

    def publish_action(self, action_data: dict[str, Any]) -> None:
        """Publish action dispatch notification."""
        self._publish(
            f"{self._publish_prefix}/action",
            action_data,
        )

    def publish_telemetry(self, data: dict[str, Any]) -> None:
        """Publish live telemetry data."""
        self._publish(
            f"{self._publish_prefix}/telemetry",
            data,
        )

    def publish_status(self, status: dict[str, Any]) -> None:
        """Publish service status."""
        self._publish(
            f"{self._publish_prefix}/status",
            status,
        )

    def publish_daily_report(self, report: dict[str, Any]) -> None:
        """Publish daily AI summary."""
        self._publish(
            f"{self._publish_prefix}/daily_report",
            report,
        )

    def publish_voice_request(self, request: dict[str, Any]) -> None:
        """Publish voice confirmation request to HA."""
        self._publish(
            "etms/voice/request",
            request,
        )

    # ── MQTT callbacks ───────────────────────────────────

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        rc: int | mqtt.ReasonCode,
        properties: Any = None,
    ) -> None:
        """Handle successful connection."""
        self._connected = True
        self._reconnect_count = 0
        logger.info("MQTT bridge connected (rc=%s)", rc)

        # Subscribe to all registered topics
        for topic in self._subscriptions:
            self._client.subscribe(topic, qos=1)
            logger.debug("Subscribed to %s", topic)

        self._publish_status("online")

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any = None,
        rc: int | mqtt.ReasonCode | None = None,
        properties: Any = None,
    ) -> None:
        """Handle disconnection."""
        self._connected = False
        self._reconnect_count += 1
        logger.warning(
            "MQTT bridge disconnected (rc=%s, reconnect #%d)",
            rc,
            self._reconnect_count,
        )

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Route incoming messages to registered callbacks."""
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = msg.payload.decode("utf-8", errors="replace")

        logger.debug("MQTT recv: %s (payload_type=%s)", msg.topic, type(payload).__name__)

        for pattern, callbacks in self._subscriptions.items():
            if mqtt.topic_matches_sub(pattern, msg.topic):
                for cb in callbacks:
                    try:
                        cb(msg.topic, payload)
                    except Exception:
                        logger.exception(
                            "Error in MQTT callback for %s",
                            msg.topic,
                        )

    # ── Private helpers ──────────────────────────────────

    def _publish(self, topic: str, data: Any) -> None:
        """Publish JSON payload to a topic."""
        if not self._connected:
            logger.warning(
                "Cannot publish to %s — not connected", topic
            )
            return

        if isinstance(data, dict):
            data["_ts"] = time.time()

        payload = json.dumps(data, default=str)
        self._client.publish(topic, payload, qos=1)

    def _publish_status(self, state: str) -> None:
        """Publish service heartbeat status."""
        self._publish(
            f"{self._publish_prefix}/status",
            {
                "state": state,
                "client_id": self._client_id,
                "reconnect_count": self._reconnect_count,
            },
        )
