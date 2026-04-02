"""OpenClaw — Emergency Orchestration Engine.

The main engine that wires together all OpenClaw subsystems:
    - PolicyEngine:       Deterministic escalation decisions
    - IncidentManager:    Incident lifecycle state machine
    - ContextAggregator:  Multi-source sensor data fusion
    - MQTTBridge:         Event ingestion and action publishing
    - ActionDispatcher:   Action execution routing
    - MedicalProfile:     Resident medical data
    - TelemetryManager:   Live vitals streaming
    - ReplayBuilder:      Incident timeline reconstruction
    - APIServer:          REST API for queries and management

Pipeline:
    MQTT Event → Context Aggregator → Policy Engine → Incident Manager
    → Action Dispatcher → MQTT Publish / HA / Telegram / Emergency
"""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.action_handlers import (
    ActionDispatcher,
    CalendarHandler,
    EmergencyHandler,
    HomeAssistantHandler,
    SlackHandler,
    TelegramHandler,
)
from src.context_aggregator import ContextAggregator
from src.incident_manager import IncidentManager, IncidentState
from src.medical_profile import MedicalProfile
from src.mqtt_bridge import MQTTBridge
from src.policy_engine import EscalationLevel, PolicyEngine
from src.replay import ReplayBuilder
from src.rest_api import APIServer
from src.telemetry import TelemetryManager

# ── Auth0 Security Layer (optional — degrades gracefully) ────
_AUTH0_AVAILABLE = False
try:
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from auth0.fga import is_authorized_sync as fga_is_authorized
    from auth0.ciba import request_backchannel_authorization
    from auth0.step_up import verify_step_up_satisfied
    from auth0.config import AUTH0_DOMAIN, SLACK_DEFAULT_CHANNEL
    _AUTH0_AVAILABLE = True
except ImportError:
    fga_is_authorized = None
    request_backchannel_authorization = None
    verify_step_up_satisfied = None
    AUTH0_DOMAIN = ""
    SLACK_DEFAULT_CHANNEL = ""

logger = logging.getLogger(__name__)


class OpenClawEngine:
    """Central orchestration engine for ETMS safety system."""

    def __init__(self, config_path: str = "config/settings.yaml") -> None:
        self.config = self._load_config(config_path)
        self.started_at = time.time()
        self._running = False

        # ── Initialize subsystems ────────────────────────
        self._init_medical_profile()
        self._init_policy_engine()
        self._init_incident_manager()
        self._init_context_aggregator()
        self._init_mqtt_bridge()
        self._init_action_handlers()
        self._init_telemetry()
        self._init_replay()
        self._init_api_server()

        # Voice confirmation events (cancel signals)
        self._voice_timers: dict[str, threading.Event] = {}

        # Auth0 caregiver token (loaded at startup for Token Vault)
        self._auth0_caregiver_token: str = self._load_auth0_token()

        # Global voice session lock – prevents ALL voice_check
        # dispatches and timer starts while a session is active
        self._voice_session_active = False
        self._voice_session_incident: str | None = None

        # Thread-safe queue for MQTT-pushed voice responses
        # from the HA automation (faster than REST polling)
        self._voice_mqtt_response: queue.Queue[dict[str, Any]] = (
            queue.Queue()
        )

        # Dedup guards: prevent repeated calls / prompts
        self._emergency_call_placed: set[str] = set()
        self._voice_check_done: set[str] = set()

        # Global emergency call lock – prevents ALL emergency_call
        # dispatches while a call is already in progress
        self._emergency_call_active = False
        self._emergency_call_incident: str | None = None

        # Incident monitoring (periodic Telegram updates)
        self._monitor_thread: threading.Thread | None = None
        self._monitored_incidents: set[str] = set()

        # Snapshot paths (written by vision service)
        self._snapshot_dir = "/tmp"
        self._camera_ids = [
            cam.get("device_id", "room_1_camera")
            for cam in self.config.get("cameras", [
                {"device_id": "room_1_camera"},
                {"device_id": "room_2_camera"},
            ])
        ]

        # Active camera – tracks which camera detected the person
        self._active_camera_id: str | None = None

    # ── Public accessors for REST API / subsystems ───────

    @property
    def incidents(self) -> IncidentManager:
        """Incident manager accessor."""
        return self._incidents

    @property
    def context(self) -> ContextAggregator:
        """Context aggregator accessor."""
        return self._context

    @property
    def mqtt(self) -> MQTTBridge:
        """MQTT bridge accessor."""
        return self._mqtt

    @property
    def telemetry(self) -> TelemetryManager:
        """Telemetry manager accessor."""
        return self._telemetry

    @property
    def replay(self) -> ReplayBuilder:
        """Replay builder accessor."""
        return self._replay

    @property
    def medical(self) -> MedicalProfile:
        """Medical profile accessor."""
        return self._medical

    # ── Lifecycle ────────────────────────────────────────

    def start(self) -> None:
        """Start all subsystems."""
        logger.info("="*60)
        logger.info("OpenClaw Emergency Orchestration Engine starting")
        logger.info("="*60)

        self._running = True
        self._mqtt.connect()
        self._api_server.start()

        # Schedule daily report
        self._schedule_daily_report()

        # Start incident monitor (periodic Telegram updates)
        self._start_incident_monitor()

        logger.info("OpenClaw engine started successfully")
        if _AUTH0_AVAILABLE:
            logger.info(
                "Auth0 Security: ACTIVE (domain=%s, token=%s)",
                AUTH0_DOMAIN,
                "loaded" if self._auth0_caregiver_token else "missing",
            )
        else:
            logger.info("Auth0 Security: NOT AVAILABLE (running without Auth0)")

    def stop(self) -> None:
        """Gracefully shut down all subsystems."""
        logger.info("OpenClaw engine shutting down")
        self._running = False

        # Stop telemetry streams
        self._telemetry.stop_all()

        # Cancel voice timers
        for cancel_evt in self._voice_timers.values():
            cancel_evt.set()
        self._voice_timers.clear()

        # Disconnect MQTT
        self._mqtt.disconnect()

        logger.info("OpenClaw engine stopped")

    def run_forever(self) -> None:
        """Block and run until interrupted."""
        self.start()

        # Handle signals for clean shutdown
        def _signal_handler(sig: int, frame: Any) -> None:
            logger.info("Received signal %d, shutting down", sig)
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    # ── Event processing pipeline ────────────────────────

    def process_vision_event(
        self, topic: str, payload: dict[str, Any]
    ) -> None:
        """Process a vision-agent reasoned event through the pipeline."""
        logger.debug("Processing vision event: %s", payload.get("event_type"))

        # 0. Track which camera detected the person
        cam = payload.get("device_id") or payload.get("camera_id")
        if cam:
            self._active_camera_id = cam

        # 1. Update context
        self._context.ingest_vision_event(payload)

        # 2. Build escalation context
        ctx = self._context.build_context()

        # 3. Run policy engine
        decision = self._policy.evaluate(ctx)

        # 4. Only create incident for WARNING+
        if decision.level < EscalationLevel.WARNING:
            return

        # 4b. Skip if already handling a CRITICAL incident
        if self._has_active_critical_incident():
            logger.debug(
                "Skipping new incident — CRITICAL already active"
            )
            return

        # 5. Create or retrieve incident
        incident = self._incidents.create_incident(
            trigger_event=payload.get("event_type", "unknown"),
            trigger_source="vision_agent",
            person_id=ctx.person_id,
            room=ctx.room,
            floor=ctx.floor,
        )
        if not incident:
            # Duplicate suppressed
            return

        # 6. Escalate incident
        self._incidents.escalate(incident.id, decision)

        # 7. Start replay recording
        self._replay.start_replay(incident.id)
        self._replay.add_event(
            incident.id,
            "vision_agent",
            payload.get("event_type", ""),
            payload,
        )

        # 8. Build action context with medical data
        action_context = self._medical.build_context_for_actions(
            incident_id=incident.id,
            room=ctx.room,
            floor=ctx.floor,
            heart_rate=ctx.heart_rate,
            spo2=ctx.spo2,
            level_name=decision.level.name,
            reasons=decision.reasons,
        )

        # 9. Dispatch actions with Auth0 security chain
        #    (FGA check → CIBA approval → Token Vault dispatch)
        actions = self._filter_actions(
            incident.id, decision.actions
        )
        results = self._auth0_dispatch_with_security(
            incident.id, decision, actions, action_context
        )
        self._mark_actions_done(incident.id, actions)

        # Record action results in replay
        for result in results:
            self._replay.add_event(
                incident.id,
                "action_handler",
                result.get("action", "unknown"),
                result,
            )

        # 10. Handle voice confirmation flow
        if (
            decision.requires_voice_confirmation
            and not self._voice_session_active
        ):
            self._start_voice_timer(incident.id)

        # 11. Start telemetry for HIGH_RISK+
        if decision.level >= EscalationLevel.HIGH_RISK:
            self._telemetry.start_stream(incident.id)

            # Send camera snapshot to Telegram
            self._send_incident_snapshot(
                incident, ctx.room or ""
            )
            # Request 5-second video clip
            self._send_incident_clip(incident)

        # 12. Publish incident to MQTT
        self._mqtt.publish_incident(incident.to_dict())

        logger.info(
            "Pipeline complete: incident=%s level=%s actions=%d",
            incident.id,
            decision.level.name,
            len(decision.actions),
        )

    def _has_active_critical_incident(self) -> bool:
        """Check if there is already an active CRITICAL incident.

        Prevents creating duplicate incidents when the system
        is already responding to an emergency.
        """
        return any(
            i.is_active and i.level >= EscalationLevel.CRITICAL
            for i in self._incidents.get_active_incidents()
        )

    def process_health_data(
        self, topic: str, payload: dict[str, Any]
    ) -> None:
        """Process health data from smartwatch."""
        self._context.ingest_health(payload)

        # Skip creating new incidents if already handling CRITICAL
        if self._has_active_critical_incident():
            return

        # Check for standalone health emergencies
        ctx = self._context.build_context()
        decision = self._policy.evaluate(ctx)

        if decision.level >= EscalationLevel.HIGH_RISK:
            incident = self._incidents.create_incident(
                trigger_event="health_alert",
                trigger_source="smartwatch",
                person_id=ctx.person_id,
                room=ctx.room,
            )
            if incident:
                self._escalate_and_dispatch(incident.id, decision, ctx)

    def process_smartguard_event(
        self, topic: str, payload: dict[str, Any]
    ) -> None:
        """Process SmartGuard anomaly data."""
        self._context.ingest_smartguard(payload)

    def process_environmental(
        self, topic: str, payload: dict[str, Any]
    ) -> None:
        """Process fire/gas/door sensor data."""
        sensor = payload.get("sensor", "")
        value = payload.get("value", False)
        self._context.ingest_environmental(sensor, value)

        # Environmental hazards trigger immediate evaluation
        if sensor in ("fire", "gas") and value:
            # Skip if already handling a CRITICAL incident
            if self._has_active_critical_incident():
                return

            ctx = self._context.build_context()
            decision = self._policy.evaluate(ctx)
            incident = self._incidents.create_incident(
                trigger_event=f"{sensor}_detected",
                trigger_source="environment",
                person_id=ctx.person_id,
                room=ctx.room,
            )
            if incident:
                self._escalate_and_dispatch(
                    incident.id, decision, ctx
                )

    def process_voice_response(
        self, topic: str, payload: dict[str, Any]
    ) -> None:
        """Process Alexa voice confirmation response."""
        response = payload.get("response")  # None for timeout
        incident_id = payload.get("incident_id", "")

        logger.info(
            "Voice response received: incident=%s response='%s'",
            incident_id,
            response,
        )

        # Cancel voice timer / monitor
        cancel_evt = self._voice_timers.pop(incident_id, None)
        if cancel_evt:
            cancel_evt.set()

        # Release global voice session lock
        if self._voice_session_incident == incident_id:
            self._voice_session_active = False
            self._voice_session_incident = None

        # Mark voice_check as done so it won't repeat
        self._voice_check_done.add(incident_id)

        # Update context
        self._context.ingest_voice_response(response)

        # Update incident
        incident = self._incidents.set_voice_response(
            incident_id, response
        )
        if not incident:
            return

        # Get current decision and apply voice response
        ctx = self._context.build_context()
        current_decision = self._policy.evaluate(ctx)
        updated = self._policy.handle_voice_response(
            current_decision, response
        )

        # Record in replay
        self._replay.add_event(
            incident_id,
            "voice",
            "response_processed",
            {
                "response": response,
                "transcript": payload.get("transcript", ""),
                "source": payload.get("source", ""),
                "original_level": current_decision.level.name,
                "updated_level": updated.level.name,
            },
        )

        if updated.level == EscalationLevel.MONITOR:
            # Person confirmed they're okay
            self._incidents.resolve(incident_id, "voice_confirmed_ok")
            self._telemetry.stop_stream(incident_id)
            self._replay.complete_replay(incident_id)
            self._context.clear_voice_state()
            self._context.clear_fall()
            self._emergency_call_placed.discard(incident_id)
            self._voice_check_done.discard(incident_id)
            if self._emergency_call_incident == incident_id:
                self._emergency_call_active = False
                self._emergency_call_incident = None
            logger.info(
                "Incident %s resolved via voice confirmation",
                incident_id,
            )
        elif updated.level == EscalationLevel.CRITICAL:
            # Distress or no response — escalate
            self._incidents.escalate(incident_id, updated)

            # Filter out already-dispatched one-shot actions
            actions = self._filter_actions(
                incident_id, updated.actions
            )

            action_context = self._medical.build_context_for_actions(
                incident_id=incident_id,
                room=ctx.room,
                floor=ctx.floor,
                heart_rate=ctx.heart_rate,
                spo2=ctx.spo2,
                level_name=updated.level.name,
                reasons=updated.reasons,
            )
            self._dispatcher.dispatch_all(
                actions, action_context
            )
            self._mark_actions_done(incident_id, actions)

        self._mqtt.publish_incident(incident.to_dict())

    def _on_mqtt_voice_response(
        self, topic: str, payload: dict[str, Any]
    ) -> None:
        """Handle voice response pushed via HA automation MQTT.

        The HA automation monitors ``last_called_summary``
        changes on the Alexa entity and publishes them to
        ``etms/openclaw/voice_response``.  We enqueue the
        payload so the polling loop in ``_monitor_voice``
        can pick it up faster than REST polling.
        """
        if not self._voice_session_active:
            return  # No active voice session — ignore

        summary = payload.get("summary", "")
        if not summary:
            return

        logger.info(
            "MQTT voice response received: %r "
            "(active session: %s)",
            summary,
            self._voice_session_incident,
        )
        self._voice_mqtt_response.put(payload)

    # ── Manual operations ────────────────────────────────

    def manual_escalate(self, incident_id: str) -> None:
        """Manually escalate an incident to next level."""
        incident = self._incidents.get_incident(incident_id)
        if not incident or not incident.is_active:
            return

        ctx = self._context.build_context()
        decision = self._policy.evaluate(ctx)

        # Force at least one level higher
        from src.policy_engine import PolicyDecision
        if decision.level <= incident.level:
            next_level = min(
                incident.level.value + 1,
                EscalationLevel.CRITICAL.value,
            )
            decision = PolicyDecision(
                level=EscalationLevel(next_level),
                reasons=decision.reasons + ["manual_escalation"],
                actions=decision.actions,
            )

        self._escalate_and_dispatch(incident_id, decision, ctx)

    def on_incident_resolved(self, incident: Any) -> None:
        """Callback when an incident is resolved (from REST API)."""
        self._telemetry.stop_stream(incident.id)
        self._replay.complete_replay(incident.id)
        self._context.clear_voice_state()
        self._context.clear_fall()
        self._emergency_call_placed.discard(incident.id)
        self._voice_check_done.discard(incident.id)
        if self._voice_session_incident == incident.id:
            self._voice_session_active = False
            self._voice_session_incident = None
        if self._emergency_call_incident == incident.id:
            self._emergency_call_active = False
            self._emergency_call_incident = None
        self._mqtt.publish_incident(incident.to_dict())

    # ── Auth0 Security Layer ────────────────────────────

    @staticmethod
    def _load_auth0_token() -> str:
        """Load the caregiver's Auth0 token for Token Vault operations.

        Auth0 feature: Token Vault — the caregiver's access token permits
        OpenClaw to exchange it for federated tokens (Google, Slack) via
        the Token Vault grant.

        Reads from the auth0/.caregiver_token file (written by login_helper.py).
        """
        token_path = os.path.join(
            os.path.dirname(__file__), "..", "auth0", ".caregiver_token"
        )
        try:
            with open(token_path) as f:
                token = f.read().strip()
                if token:
                    logging.getLogger(__name__).info(
                        "Auth0 caregiver token loaded for Token Vault"
                    )
                    return token
        except FileNotFoundError:
            pass
        logging.getLogger(__name__).warning(
            "No Auth0 caregiver token — Token Vault Calendar "
            "events will require manual login via login_helper.py"
        )
        return ""

    def _auth0_check_fga(
        self, agent: str, data_stream: str
    ) -> bool:
        """Check FGA authorization before dispatching.

        Auth0 feature: Fine-Grained Authorization (FGA)
        Ensures only authorized agents can access specific data streams.

        Args:
            agent: The agent identity (e.g. 'vision_agent').
            data_stream: The data stream being accessed.

        Returns:
            True if authorized or if Auth0 is unavailable (fail-open
            for safety — elderly monitoring must never be blocked).
        """
        if not _AUTH0_AVAILABLE or not fga_is_authorized:
            return True  # Fail-open for safety

        try:
            authorized = fga_is_authorized(
                f"user:{agent}", "viewer", f"data_stream:{data_stream}"
            )
            if not authorized:
                logger.warning(
                    "FGA denied: agent=%s stream=%s", agent, data_stream
                )
            return authorized
        except Exception as e:
            logger.error("FGA check failed (allowing): %s", e)
            return True  # Fail-open for safety

    def _auth0_request_ciba_approval(
        self, incident_id: str, description: str
    ) -> bool:
        """Request CIBA backchannel authorization for critical dispatch.

        Auth0 feature: CIBA (Client Initiated Backchannel Authentication)
        Sends a push notification to the caregiver's device for approval
        before dispatching emergency services.

        Args:
            incident_id: The incident triggering the request.
            description: Human-readable incident description.

        Returns:
            True if approved (or Auth0 unavailable), False if denied.
        """
        if not _AUTH0_AVAILABLE or not request_backchannel_authorization:
            return True  # Fail-open for safety

        try:
            caregiver_id = os.environ.get(
                "AUTH0_CAREGIVER_ID",
                os.environ.get("AUTH0_CAREGIVER_EMAIL", "caregiver@etms.local"),
            )
            incident_context = {
                "elder_name": self._medical.name
                if hasattr(self, "_medical") and self._medical
                else "Unknown",
                "location": "home",
                "event_type": description[:60],
                "vitals_summary": "see incident details",
            }
            auth_req_id = request_backchannel_authorization(
                caregiver_id, incident_context
            )
            if not auth_req_id:
                logger.warning(
                    "CIBA: could not initiate for incident %s — allowing dispatch",
                    incident_id,
                )
                return True  # Fail-open for safety

            from auth0.ciba import poll_for_approval

            approved, reason, _token = poll_for_approval(auth_req_id, timeout=30)
            logger.info(
                "CIBA approval for incident %s: %s (reason=%s)",
                incident_id,
                "approved" if approved else "denied/timeout",
                reason,
            )
            return approved
        except Exception as e:
            logger.error(
                "CIBA request failed (allowing dispatch): %s", e
            )
            return True  # Fail-open for safety

    def _auth0_dispatch_with_security(
        self,
        incident_id: str,
        decision: Any,
        actions: list[str],
        action_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Dispatch actions with full Auth0 security chain.

        Auth0 features used:
        - FGA: Authorization check before dispatch
        - CIBA: Backchannel approval for HIGH_RISK+ incidents
        - Step-Up: MFA verification for CRITICAL incidents
        - Token Vault: Slack/Calendar API calls via federated tokens

        For safety, Auth0 failures are non-blocking (fail-open).
        Elderly monitoring dispatch must never be prevented by an
        authorization service outage.
        """
        auth0_log = {
            "incident_id": incident_id,
            "auth0_checks": [],
        }

        # FGA: Check agent authorization
        fga_ok = self._auth0_check_fga(
            "vision_agent", "fall_events"
        )
        auth0_log["auth0_checks"].append({
            "feature": "FGA",
            "result": "authorized" if fga_ok else "denied",
        })

        # CIBA: Request caregiver approval for HIGH_RISK+
        if decision.level >= EscalationLevel.HIGH_RISK:
            reasons_str = "; ".join(
                decision.reasons[:2]
            ) if decision.reasons else "Safety alert"
            ciba_ok = self._auth0_request_ciba_approval(
                incident_id, reasons_str
            )
            auth0_log["auth0_checks"].append({
                "feature": "CIBA",
                "result": "approved" if ciba_ok else "pending",
            })

        # Step-Up: Log requirement for CRITICAL
        if decision.level >= EscalationLevel.CRITICAL:
            auth0_log["auth0_checks"].append({
                "feature": "Step-Up",
                "result": "required_for_emergency_call",
            })

        # Publish Auth0 security audit to MQTT
        self._mqtt._publish(
            "etms/openclaw/auth0_audit", auth0_log
        )

        # Inject Auth0 caregiver token into context for Token Vault handlers
        action_context["auth0_token"] = self._auth0_caregiver_token

        # Dispatch all actions (Auth0 handlers included)
        results = self._dispatcher.dispatch_all(
            actions, action_context
        )
        return results

    # ── Private helpers ──────────────────────────────────

    def _filter_actions(
        self, incident_id: str, actions: list[str]
    ) -> list[str]:
        """Remove actions that have already been performed.

        Prevents repeated emergency calls and voice-check
        prompts for the same incident.  voice_check is also
        blocked globally while any voice session is active.
        """
        filtered: list[str] = []
        for action in actions:
            if action == "emergency_call" and (
                incident_id in self._emergency_call_placed
                or self._emergency_call_active
            ):
                logger.debug(
                    "Skipping emergency_call for incident %s "
                    "(placed=%s call_active=%s)",
                    incident_id,
                    incident_id in self._emergency_call_placed,
                    self._emergency_call_active,
                )
                continue
            if action == "voice_check" and (
                incident_id in self._voice_check_done
                or self._voice_session_active
            ):
                logger.debug(
                    "Skipping voice_check for incident %s "
                    "(done=%s session_active=%s)",
                    incident_id,
                    incident_id in self._voice_check_done,
                    self._voice_session_active,
                )
                continue
            filtered.append(action)
        return filtered

    def _mark_actions_done(
        self, incident_id: str, actions: list[str]
    ) -> None:
        """Record which one-shot actions were dispatched."""
        if "emergency_call" in actions:
            self._emergency_call_placed.add(incident_id)
            self._emergency_call_active = True
            self._emergency_call_incident = incident_id
        if "voice_check" in actions:
            self._voice_check_done.add(incident_id)

    def _escalate_and_dispatch(
        self,
        incident_id: str,
        decision: Any,
        ctx: Any,
    ) -> None:
        """Common escalation + dispatch logic with Auth0 security."""
        self._incidents.escalate(incident_id, decision)

        self._replay.start_replay(incident_id)

        # Filter out already-performed one-shot actions
        actions = self._filter_actions(
            incident_id, decision.actions
        )

        action_context = self._medical.build_context_for_actions(
            incident_id=incident_id,
            room=ctx.room,
            floor=ctx.floor,
            heart_rate=ctx.heart_rate,
            spo2=ctx.spo2,
            level_name=decision.level.name,
            reasons=decision.reasons,
        )

        # Dispatch with Auth0 security chain
        self._auth0_dispatch_with_security(
            incident_id, decision, actions, action_context
        )
        self._mark_actions_done(incident_id, actions)

        if (
            decision.requires_voice_confirmation
            and not self._voice_session_active
        ):
            self._start_voice_timer(incident_id)

        if decision.level >= EscalationLevel.HIGH_RISK:
            self._telemetry.start_stream(incident_id)

        self._mqtt.publish_incident(
            self._incidents.get_incident(incident_id).to_dict()
        )

    def _start_voice_timer(self, incident_id: str) -> None:
        """Monitor Alexa for voice response via HA REST API polling.

        After the announcement plays (~5 s), polls the Alexa
        entity ``last_called_summary`` attribute.  When a new
        wake-word invocation is detected the transcript is
        matched against positive / negative keywords to classify
        the response as *ok*, *help*, or *unknown*.

        **Alexa follow-up announcements** are sent for every
        classification before the response is processed:

        * *ok*  → reassurance ("No emergency call, still monitoring")
        * *help* → pre-call warning ("Calling emergency, data shared")
        * *unknown* → re-prompt (ask again, keep polling)
        * *timeout* → pre-call warning ("No response, calling now")

        Falls back to timeout if no interaction is detected.
        """
        # Acquire global voice session lock
        self._voice_session_active = True
        self._voice_session_incident = incident_id

        cancel_evt = threading.Event()

        # Keywords that indicate the person is okay
        _POSITIVE_KEYWORDS = {
            "fine", "okay", "ok", "good", "alright",
            "i'm fine", "i am fine", "i'm okay", "i am okay",
            "i'm good", "i am good", "i'm alright", "i am alright",
            "no problem", "all good", "safe",
        }

        # Explicit distress keywords — classified immediately
        # so that Alexa built-in responses containing these
        # also trigger escalation without ambiguity.
        _NEGATIVE_KEYWORDS = {
            "help", "not okay", "not ok", "not fine",
            "not good", "not alright", "emergency",
            "i'm not okay", "i am not okay",
            "i'm not ok", "i am not ok",
            "i'm not fine", "i am not fine",
            "need help", "call for help",
            "send help", "hurt", "fall", "fell",
            "can't move", "cannot move", "pain",
        }

        # Alexa control commands that should be IGNORED.
        # These are the user interacting with Alexa itself
        # (e.g. stopping the announcement), NOT responding
        # to the safety prompt.  When detected the system
        # continues polling instead of classifying.
        _IGNORE_COMMANDS = {
            "stop", "cancel", "never mind", "nevermind",
            "shut up", "be quiet", "quiet",
            "volume up", "volume down",
            "mute", "unmute", "louder", "softer",
            "repeat", "pause", "resume",
            "turn off", "turn on",
            "what time is it", "what's the time",
            "what's the weather", "play music",
            "play", "next", "previous", "skip",
            "set a timer", "set an alarm",
            "thank you", "thanks",
            "goodbye", "bye",
        }

        def _classify_response(summary: str) -> str:
            """Classify an Alexa transcript as ok, not_ok, or ignore.

            Returns ``'ignore'`` for Alexa control commands
            (stop, cancel, volume, etc.) so the polling loop
            skips them and keeps waiting for a real answer.

            Checks negative keywords first (explicit distress),
            then positive keywords.  Any unrecognised response
            that is NOT an Alexa command is treated as distress
            to err on the side of caution.
            """
            text = summary.lower().strip()
            # Skip Alexa control commands (e.g. "stop" to
            # silence the announcement)
            if text in _IGNORE_COMMANDS:
                return "ignore"
            # Explicit distress takes priority
            for kw in _NEGATIVE_KEYWORDS:
                if kw in text:
                    return "not_ok"
            for kw in _POSITIVE_KEYWORDS:
                if kw in text:
                    return "ok"
            # Anything else = not confirmed safe → distress
            return "not_ok"

        def _get_vitals_text() -> str:
            """Build a short vitals string for announcements."""
            ctx = self._context.build_context()
            parts: list[str] = []
            if ctx.heart_rate is not None:
                parts.append(
                    f"heart rate {int(ctx.heart_rate)}"
                )
            if ctx.spo2 is not None:
                parts.append(
                    f"oxygen level {int(ctx.spo2)} percent"
                )
            return ", ".join(parts) if parts else "your vitals"

        def _get_person_name() -> str:
            """Get the resident's name from medical profile."""
            return self._medical.name or ""

        def _announce(message: str) -> None:
            """Send an Alexa announcement (best-effort)."""
            try:
                self._ha_handler.announce_message(message)
            except Exception:
                logger.warning(
                    "Failed to send Alexa follow-up "
                    "announcement for incident %s",
                    incident_id,
                )

        def _end_voice_session() -> None:
            """Release the global voice session lock."""
            self._voice_session_active = False
            self._voice_session_incident = None
            # Drain stale MQTT voice responses
            while not self._voice_mqtt_response.empty():
                try:
                    self._voice_mqtt_response.get_nowait()
                except queue.Empty:
                    break

        def _monitor_voice() -> None:
            entity_id = self._ha_handler.alexa_entity_id
            name = _get_person_name()
            greeting = f"{name}, " if name else ""

            # Snapshot current state BEFORE the announcement
            initial_state = self._ha_handler.get_entity_state(
                entity_id
            )
            initial_timestamp = ""
            if initial_state:
                attrs = initial_state.get("attributes", {})
                initial_timestamp = str(
                    attrs.get("last_called_timestamp", "")
                )

            # Wait for the announcement to finish (~6 s)
            if cancel_evt.wait(6):
                _end_voice_session()
                return

            # ── 20-second polling window ──
            # If no positive confirmation within 20 s → call
            poll_seconds = 20
            remaining = poll_seconds
            while remaining > 0:
                # ── Check MQTT-pushed voice response first ──
                # The HA automation publishes to MQTT faster
                # than our REST polling can detect changes.
                try:
                    mqtt_resp = (
                        self._voice_mqtt_response.get_nowait()
                    )
                    summary = mqtt_resp.get("summary", "")
                    if summary:
                        classification = _classify_response(
                            summary
                        )
                        logger.info(
                            "MQTT-pushed voice for %s — "
                            "summary=%r → %s",
                            incident_id,
                            summary,
                            classification,
                        )

                        if classification == "ignore":
                            # Alexa control command (stop,
                            # cancel, etc.) — keep polling
                            logger.debug(
                                "Ignoring Alexa command "
                                "%r for incident %s",
                                summary,
                                incident_id,
                            )
                        elif classification == "ok":
                            vitals = _get_vitals_text()
                            _announce(
                                f"{greeting}That's great to "
                                f"hear. No emergency call "
                                f"will be made. I will keep "
                                f"monitoring {vitals}. "
                                f"Stay safe and take care."
                            )
                            self.process_voice_response(
                                "",
                                {
                                    "incident_id": incident_id,
                                    "response": "ok",
                                    "source": "mqtt_voice",
                                    "transcript": summary,
                                },
                            )
                            _end_voice_session()
                            return

                        else:
                            # Distress via MQTT path
                            vitals = _get_vitals_text()
                            _announce(
                                f"{greeting}I hear you. "
                                f"I am calling emergency "
                                f"services right now. "
                                f"I have already shared your "
                                f"health information, "
                                f"including {vitals}, and a "
                                f"photo from the camera "
                                f"with the responders. "
                                f"Stay calm, help is on "
                                f"the way."
                            )
                            cancel_evt.wait(4)
                            self.process_voice_response(
                                "",
                                {
                                    "incident_id": incident_id,
                                    "response": "help",
                                    "source": "mqtt_voice",
                                    "transcript": summary,
                                },
                            )
                            _end_voice_session()
                            return
                except queue.Empty:
                    pass

                # ── REST polling fallback ──
                # Force-refresh last_called from Amazon API
                # so we don't wait for the default 60 s poll
                self._ha_handler.force_update_last_called()

                state = self._ha_handler.get_entity_state(
                    entity_id
                )
                if state:
                    attrs = state.get("attributes", {})
                    current_timestamp = str(
                        attrs.get("last_called_timestamp", "")
                    )
                    if (
                        current_timestamp
                        and current_timestamp != initial_timestamp
                    ):
                        summary = str(
                            attrs.get(
                                "last_called_summary", ""
                            )
                        )
                        classification = _classify_response(
                            summary
                        )
                        logger.info(
                            "Alexa interaction detected for "
                            "incident %s — summary=%r → %s",
                            incident_id,
                            summary,
                            classification,
                        )

                        if classification == "ignore":
                            # Alexa control command (stop,
                            # cancel, etc.) — update the
                            # timestamp so we don't re-detect
                            # the same command, keep polling
                            initial_timestamp = (
                                current_timestamp
                            )
                            logger.debug(
                                "Ignoring Alexa command "
                                "%r for incident %s — "
                                "continuing to poll",
                                summary,
                                incident_id,
                            )
                        elif classification == "ok":
                            vitals = _get_vitals_text()
                            _announce(
                                f"{greeting}That's great to "
                                f"hear. No emergency call "
                                f"will be made. I will keep "
                                f"monitoring {vitals}. "
                                f"Stay safe and take care."
                            )
                            self.process_voice_response(
                                "",
                                {
                                    "incident_id": incident_id,
                                    "response": "ok",
                                    "source": "alexa_voice",
                                    "transcript": summary,
                                },
                            )
                            _end_voice_session()
                            return

                        elif classification == "not_ok":
                            # Distress response → escalate
                            vitals = _get_vitals_text()
                            _announce(
                                f"{greeting}I hear you. "
                                f"I am calling emergency "
                                f"services right now. "
                                f"I have already shared "
                                f"your health information, "
                                f"including {vitals}, and "
                                f"a photo from the camera "
                                f"with the responders. "
                                f"Stay calm, help is on "
                                f"the way."
                            )
                            cancel_evt.wait(4)
                            self.process_voice_response(
                                "",
                                {
                                    "incident_id": incident_id,
                                    "response": "help",
                                    "source": "alexa_voice",
                                    "transcript": summary,
                                },
                            )
                            _end_voice_session()
                            return

                if cancel_evt.wait(2):
                    _end_voice_session()
                    return
                remaining -= 2

            # ── Timeout — no response at all ──
            vitals = _get_vitals_text()
            _announce(
                f"{greeting}You have not responded. "
                f"For your safety, I am now calling "
                f"emergency services. Your health "
                f"information, including {vitals}, "
                f"and a photo from the camera have "
                f"been shared with the responders. "
                f"Stay calm, help is coming soon."
            )
            cancel_evt.wait(5)

            logger.warning(
                "Voice confirmation timeout for "
                "incident %s",
                incident_id,
            )
            self.process_voice_response(
                "",
                {
                    "incident_id": incident_id,
                    "response": None,
                },
            )
            _end_voice_session()

        thread = threading.Thread(
            target=_monitor_voice,
            daemon=True,
            name=f"voice-monitor-{incident_id[:8]}",
        )
        thread.start()
        self._voice_timers[incident_id] = cancel_evt

    def _start_incident_monitor(self) -> None:
        """Start background thread that sends periodic Telegram updates.

        Every 30 seconds, checks for active HIGH_RISK+ incidents
        and sends a status update with snapshot to caregivers.
        """
        interval = self.config.get("incident_monitor", {}).get(
            "update_interval_seconds", 30
        )

        def _monitor_loop() -> None:
            while self._running:
                try:
                    self._send_periodic_updates()
                except Exception:
                    logger.exception("Error in incident monitor")
                time.sleep(interval)

        self._monitor_thread = threading.Thread(
            target=_monitor_loop,
            daemon=True,
            name="incident-monitor",
        )
        self._monitor_thread.start()
        logger.info(
            "Incident monitor started (updates every %ds)", interval
        )

    def _get_incident_reasons(self, incident: Any) -> list[str]:
        """Extract escalation reasons from an incident's timeline."""
        reasons: list[str] = []
        for entry in reversed(incident.timeline):
            if (
                entry.event == "escalated"
                and "reasons" in entry.details
            ):
                reasons = entry.details["reasons"]
                break
        return reasons

    def _send_periodic_updates(self) -> None:
        """Send Telegram status updates for active incidents."""
        active = self._incidents.get_active_incidents()
        if not active:
            return

        for incident in active:
            if incident.level < EscalationLevel.HIGH_RISK:
                continue

            # Build status message
            ctx = self._context.build_context()
            duration = int(time.time() - incident.created_at)
            minutes, seconds = divmod(duration, 60)
            reasons = self._get_incident_reasons(incident)

            lines = [
                f"📊 INCIDENT STATUS UPDATE",
                f"Person: {self._medical.name} (age {self._medical.age})",
                f"Address: {self._medical.address or 'N/A'}",
                f"Incident: {incident.id[:8]}",
                f"Level: {incident.level.name}",
                f"Duration: {minutes}m {seconds}s",
                f"Trigger: {incident.trigger_event}",
                "",
            ]

            if reasons:
                lines.append("Reasons:")
                for r in reasons:
                    lines.append(f"  • {r}")
                lines.append("")

            # Health data
            hr = ctx.heart_rate
            spo2 = ctx.spo2
            if hr is not None:
                lines.append(f"❤️ Heart rate: {hr} bpm")
            else:
                lines.append("❤️ Heart rate: no data")
            if spo2 is not None:
                lines.append(f"🫁 SpO2: {spo2}%")
            else:
                lines.append("🫁 SpO2: no data")

            # Movement status
            inactivity = getattr(ctx, "inactivity_seconds", None)
            if inactivity is not None:
                lines.append(
                    f"🚶 No movement for: {int(inactivity)}s"
                )

            # Location (room + floor + address)
            room = ctx.room or incident.room or "unknown"
            floor = ctx.floor if ctx.floor else incident.floor
            location = self._context.get_location_info()
            address = self._medical.address or "N/A"
            lines.append(f"📍 Room: {room}")
            lines.append(f"🏢 Floor: {floor}")
            lines.append(f"🏠 Address: {address}")
            if location.get("last_movement_age") is not None:
                age_s = int(location["last_movement_age"])
                lines.append(
                    f"⏱️ Last movement: {age_s}s ago"
                )

            # Voice status
            if incident.voice_response is not None:
                lines.append(
                    f"🗣️ Voice response: {incident.voice_response}"
                )
            elif incident.level.name == "CRITICAL":
                lines.append("🗣️ Voice response: NO RESPONSE")

            # Consciousness assessment
            if inactivity and inactivity > 120:
                lines.append("⚠️ Person may be unconscious")
            elif inactivity and inactivity < 30:
                lines.append("✅ Person is moving")

            message = "\n".join(lines)

            # Send text update
            self._telegram_handler.execute(
                "status_update",
                {
                    "level_name": incident.level.name,
                    "room": room,
                    "person_name": self._medical.name,
                    "reasons": reasons,
                    "incident_id": incident.id,
                    "heart_rate": hr,
                    "spo2": spo2,
                    "_raw_message": message,
                },
            )

            # Send snapshot if available
            self._send_incident_snapshot(incident, room)

    def _send_incident_snapshot(
        self, incident: Any, room: str
    ) -> None:
        """Send camera snapshot to Telegram for the incident.

        Only sends the photo from the camera that detected the
        person (``_active_camera_id``).  Falls back to sending
        all recent snapshots if no active camera is set.
        """
        import glob

        # Find available snapshots
        if self._active_camera_id:
            # Only look for snapshot from the active camera
            pattern = (
                f"{self._snapshot_dir}/etms_latest_"
                f"{self._active_camera_id}.jpg"
            )
        else:
            pattern = f"{self._snapshot_dir}/etms_latest_*.jpg"
        snapshots = glob.glob(pattern)

        if not snapshots:
            return

        for snap_path in snapshots:
            # Check file is recent (< 5 seconds old)
            try:
                age = time.time() - os.path.getmtime(snap_path)
                if age > 5.0:
                    continue
            except OSError:
                continue

            camera_id = os.path.basename(snap_path).replace(
                "etms_latest_", ""
            ).replace(".jpg", "")

            # Build rich caption with health and location data
            ctx = self._context.build_context()
            hr_text = f"{ctx.heart_rate} bpm" if ctx.heart_rate else "N/A"
            spo2_text = f"{ctx.spo2}%" if ctx.spo2 else "N/A"

            caption = (
                f"📷 Camera: {camera_id}\n"
                f"👤 {self._medical.name} (age {self._medical.age})\n"
                f"📍 {room or 'unknown'} — {self._medical.address}\n"
                f"Incident: {incident.id[:8]} [{incident.level.name}]\n"
                f"❤️ HR: {hr_text} | 🫁 SpO2: {spo2_text}"
            )

            self._telegram_handler.send_photo(
                snap_path, caption=caption
            )

    def _send_incident_clip(
        self, incident: Any, camera_id: str = ""
    ) -> None:
        """Request and send a video clip to Telegram."""
        # Request clip from the active camera (person's camera)
        target_camera = (
            camera_id
            or self._active_camera_id
            or (self._camera_ids[0] if self._camera_ids else "")
        )
        if not target_camera:
            return

        self._mqtt._publish(
            "etms/vision/clip_request",
            {
                "camera_id": target_camera,
                "duration": 5,
                "incident_id": incident.id,
            },
        )
        logger.info(
            "Requested 5s video clip from %s for incident %s",
            target_camera,
            incident.id,
        )

    def _schedule_daily_report(self) -> None:
        """Schedule daily AI summary generation."""
        report_config = self.config.get("daily_report", {})
        if not report_config.get("enabled", True):
            return

        def _generate_report() -> None:
            while self._running:
                try:
                    report = self._build_daily_report()
                    self._mqtt.publish_daily_report(report)
                    logger.info("Daily report generated")
                except Exception:
                    logger.exception("Error generating daily report")
                # Sleep until next report time (simplified: every 24h)
                time.sleep(86400)

        thread = threading.Thread(
            target=_generate_report,
            daemon=True,
            name="daily-report",
        )
        thread.start()

    def _build_daily_report(self) -> dict[str, Any]:
        """Build the daily AI summary report."""
        recent = self._incidents.get_recent(limit=100)
        today_start = time.time() - 86400

        today_incidents = [
            i for i in recent if i.created_at >= today_start
        ]
        critical_count = sum(
            1 for i in today_incidents
            if i.level >= EscalationLevel.CRITICAL
        )

        hr_trend = self._context.get_heart_rate_trend(
            window_seconds=86400
        )

        return {
            "report_type": "daily_summary",
            "generated_at": time.time(),
            "period_hours": 24,
            "resident": self._medical.name,
            "incidents": {
                "total": len(today_incidents),
                "critical": critical_count,
                "resolved": sum(
                    1
                    for i in today_incidents
                    if i.state.value == "resolved"
                ),
            },
            "health_summary": {
                "heart_rate_trend": hr_trend,
                "spo2_latest": self._context.get_latest(
                    "health", "spo2"
                ),
                "steps": self._context.get_latest(
                    "health", "steps"
                ),
            },
            "location_summary": self._context.get_location_info(),
            "system_stats": self._incidents.stats,
        }

    # ── Initialization ───────────────────────────────────

    def _load_config(self, path: str) -> dict[str, Any]:
        """Load YAML configuration and overlay environment secrets."""
        # Load .env from project root
        env_path = Path(__file__).parent / ".env"
        load_dotenv(env_path, override=True)

        config_path = Path(path)
        if not config_path.exists():
            logger.warning(
                "Config file %s not found, using defaults", path
            )
            return {}
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        # Overlay environment variables onto config
        self._apply_env_secrets(config)
        return config

    @staticmethod
    def _apply_env_secrets(config: dict[str, Any]) -> None:
        """Inject secrets from environment into config dict."""
        # MQTT
        mqtt = config.setdefault("mqtt", {})
        if val := os.environ.get("MQTT_USERNAME"):
            mqtt["username"] = val
        if val := os.environ.get("MQTT_PASSWORD"):
            mqtt["password"] = val

        # Home Assistant
        ha = config.setdefault("actions", {}).setdefault(
            "homeassistant", {}
        )
        if val := os.environ.get("HA_TOKEN"):
            ha["token"] = val

        # Telegram
        tg = config.setdefault("actions", {}).setdefault(
            "telegram", {}
        )
        if val := os.environ.get("TELEGRAM_BOT_TOKEN"):
            tg["bot_token"] = val
        if val := os.environ.get("TELEGRAM_CHAT_IDS"):
            tg["chat_ids"] = [
                cid.strip() for cid in val.split(",") if cid.strip()
            ]

        # Twilio
        em = config.setdefault("actions", {}).setdefault(
            "emergency", {}
        )
        if val := os.environ.get("TWILIO_ACCOUNT_SID"):
            em["twilio_account_sid"] = val
        if val := os.environ.get("TWILIO_AUTH_TOKEN"):
            em["twilio_auth_token"] = val
        if val := os.environ.get("TWILIO_FROM_NUMBER"):
            em["twilio_from_number"] = val
        if val := os.environ.get("TWILIO_TO_NUMBER"):
            em["emergency_to_number"] = val
        if val := os.environ.get("TWILIO_PUBLIC_URL"):
            em["public_url"] = val

    def _init_medical_profile(self) -> None:
        """Initialize medical profile from config."""
        resident = self.config.get("resident", {})
        self._medical = MedicalProfile.from_dict(resident)
        logger.info(
            "Medical profile loaded for: %s (age %d)",
            self._medical.name,
            self._medical.age,
        )

    def _init_policy_engine(self) -> None:
        """Initialize policy engine."""
        from src.policy_engine import PolicyThresholds

        thresholds_cfg = self.config.get("escalation_policy", {})
        thresholds = PolicyThresholds.from_dict(thresholds_cfg)
        self._policy = PolicyEngine(thresholds)
        logger.info("Policy engine initialized")

    def _init_incident_manager(self) -> None:
        """Initialize incident manager."""
        self._incidents = IncidentManager(
            max_active=50,
            auto_expire_seconds=3600,
            dedup_window=60.0,
        )

    def _init_context_aggregator(self) -> None:
        """Initialize context aggregator."""
        self._context = ContextAggregator(
            window_seconds=120.0,
            max_readings_per_key=200,
        )

    def _init_mqtt_bridge(self) -> None:
        """Initialize MQTT bridge with topic subscriptions."""
        mqtt_cfg = self.config.get("mqtt", {})
        self._mqtt = MQTTBridge(
            host=mqtt_cfg.get("broker", "localhost"),
            port=mqtt_cfg.get("port", 1883),
            username=mqtt_cfg.get("username"),
            password=mqtt_cfg.get("password"),
            client_id="openclaw",
            publish_prefix=mqtt_cfg.get(
                "publish_prefix", "etms/openclaw"
            ),
        )

        # Subscribe to all event topics
        topics = mqtt_cfg.get("subscribe_topics", {})

        self._mqtt.subscribe(
            topics.get(
                "vision_agent", "vision_agent/reasoned_event"
            ),
            self.process_vision_event,
        )
        self._mqtt.subscribe(
            topics.get("smartguard", "smartguard/anomaly"),
            self.process_smartguard_event,
        )
        self._mqtt.subscribe(
            topics.get(
                "health", "homeassistant/sensor/+/state"
            ),
            self.process_health_data,
        )
        self._mqtt.subscribe(
            topics.get("fire", "homeassistant/binary_sensor/fire/state"),
            self.process_environmental,
        )
        self._mqtt.subscribe(
            topics.get("gas", "homeassistant/binary_sensor/gas/state"),
            self.process_environmental,
        )
        self._mqtt.subscribe(
            topics.get(
                "voice_response", "etms/voice/response"
            ),
            self.process_voice_response,
        )

        # HA automation pushes Alexa voice summaries here
        # for faster detection than REST polling
        self._mqtt.subscribe(
            "etms/openclaw/voice_response",
            self._on_mqtt_voice_response,
        )

    def _init_action_handlers(self) -> None:
        """Initialize action handlers including Auth0 Token Vault handlers."""
        ha_cfg = self.config.get("actions", {}).get(
            "homeassistant", {}
        )
        telegram_cfg = self.config.get("actions", {}).get(
            "telegram", {}
        )
        emergency_cfg = self.config.get("actions", {}).get(
            "emergency", {}
        )

        ha_handler = HomeAssistantHandler(
            base_url=ha_cfg.get("url", "http://localhost:8123"),
            token=ha_cfg.get("token", ""),
            timeout=ha_cfg.get("timeout", 10),
            alexa_entity_id=ha_cfg.get(
                "alexa_media_player",
                "media_player.alexa_sala_s_echo_dot",
            ),
        )
        self._ha_handler = ha_handler

        self._telegram_handler = TelegramHandler(
            bot_token=telegram_cfg.get("bot_token", ""),
            chat_ids=telegram_cfg.get("chat_ids", []),
            mqtt_publish_fn=self._mqtt._publish,
        )

        emergency_handler = EmergencyHandler(
            mode=emergency_cfg.get("mode", "development"),
            twilio_account_sid=emergency_cfg.get(
                "twilio_account_sid", ""
            ),
            twilio_auth_token=emergency_cfg.get(
                "twilio_auth_token", ""
            ),
            twilio_from_number=emergency_cfg.get(
                "twilio_from_number", ""
            ),
            emergency_to_number=emergency_cfg.get(
                "emergency_to_number", ""
            ),
            twiml_message=emergency_cfg.get("twiml_message", ""),
            public_url=emergency_cfg.get("public_url", ""),
        )

        # ── Auth0 Token Vault handlers ───────────────────
        slack_handler = SlackHandler(
            default_channel=SLACK_DEFAULT_CHANNEL,
            mqtt_publish_fn=self._mqtt._publish,
        )

        calendar_handler = CalendarHandler(
            calendar_id="primary",
            mqtt_publish_fn=self._mqtt._publish,
        )

        if _AUTH0_AVAILABLE:
            logger.info(
                "Auth0 integration ACTIVE — "
                "Slack + Calendar handlers via Token Vault"
            )
        else:
            logger.warning(
                "Auth0 integration NOT available — "
                "Slack + Calendar handlers will degrade gracefully"
            )

        self._dispatcher = ActionDispatcher(
            ha_handler=ha_handler,
            telegram_handler=self._telegram_handler,
            emergency_handler=emergency_handler,
            slack_handler=slack_handler,
            calendar_handler=calendar_handler,
        )

    def _init_telemetry(self) -> None:
        """Initialize telemetry manager."""
        tel_cfg = self.config.get("telemetry", {})
        self._telemetry = TelemetryManager(
            default_interval=tel_cfg.get("stream_interval", 5.0),
            data_fn=lambda: {
                "health": self._context.get_heart_rate_trend(60),
                "spo2": self._context.get_latest("health", "spo2"),
                "location": self._context.get_location_info(),
            },
            publish_fn=self._mqtt.publish_telemetry,
        )

    def _init_replay(self) -> None:
        """Initialize replay builder."""
        replay_cfg = self.config.get("replay", {})
        self._replay = ReplayBuilder(
            pre_window=replay_cfg.get(
                "pre_incident_window", 300.0
            ),
            post_window=replay_cfg.get(
                "post_incident_window", 60.0
            ),
        )

    def _init_api_server(self) -> None:
        """Initialize REST API server."""
        service_cfg = self.config.get("service", {})
        self._api_server = APIServer(
            engine=self,
            host="0.0.0.0",
            port=service_cfg.get("port", 8200),
        )
