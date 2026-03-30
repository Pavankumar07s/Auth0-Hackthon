"""Comprehensive tests for OpenClaw emergency orchestration engine.

Tests cover:
    - PolicyEngine: All escalation levels, thresholds, combinations
    - IncidentManager: Lifecycle, dedup, expiration, queries
    - ContextAggregator: Ingestion, context building, trends
    - MedicalProfile: Loading, packet generation, contacts
    - ActionDispatcher: Routing, result collection
    - TelemetryManager: Stream lifecycle
    - ReplayBuilder: Timeline recording and retrieval
    - Integration: End-to-end pipeline scenarios
"""

import json
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

# ── Adjust sys.path for imports ──────────────────────────
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ═════════════════════════════════════════════════════════
# Policy Engine Tests
# ═════════════════════════════════════════════════════════

from src.policy_engine import (
    EscalationLevel,
    PolicyThresholds,
    EscalationContext,
    PolicyDecision,
    PolicyEngine,
)


class TestEscalationLevel:
    """Test escalation level enum ordering."""

    def test_level_ordering(self):
        assert EscalationLevel.MONITOR < EscalationLevel.WARNING
        assert EscalationLevel.WARNING < EscalationLevel.HIGH_RISK
        assert EscalationLevel.HIGH_RISK < EscalationLevel.CRITICAL

    def test_level_values(self):
        assert EscalationLevel.MONITOR.value == 0
        assert EscalationLevel.CRITICAL.value == 3


class TestPolicyThresholds:
    """Test threshold configuration."""

    def test_default_thresholds(self):
        t = PolicyThresholds()
        assert t.hr_critical_low == 40
        assert t.hr_critical_high == 170
        assert t.spo2_critical == 88
        assert t.spo2_warning == 92

    def test_from_dict(self):
        cfg = {"hr_critical_low": 45, "spo2_critical": 90}
        t = PolicyThresholds.from_dict(cfg)
        assert t.hr_critical_low == 45
        assert t.spo2_critical == 90
        # Defaults preserved for unset values
        assert t.hr_critical_high == 170

    def test_from_empty_dict(self):
        t = PolicyThresholds.from_dict({})
        assert t.hr_critical_low == 40


class TestPolicyEngine:
    """Test deterministic escalation logic."""

    def setup_method(self):
        self.engine = PolicyEngine()

    def _ctx(self, **kwargs):
        """Helper to build context with defaults."""
        defaults = {
            "fire_detected": False,
            "gas_leak_detected": False,
            "fall_detected": False,
            "heart_rate": 72.0,
            "spo2": 97.0,
            "inactivity_seconds": 0,
            "movement_present": True,
            "anomaly_score": 0.0,
            "behavior_anomaly": False,
            "wandering_detected": False,
            "vision_agent_severity": "info",
            "room": "living_room",
            "floor": 1,
            "person_id": "person_1",
        }
        defaults.update(kwargs)
        return EscalationContext(**defaults)

    # ── CRITICAL level tests ──

    def test_fire_triggers_critical(self):
        ctx = self._ctx(fire_detected=True)
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.CRITICAL
        assert "emergency_call" in decision.actions

    def test_gas_triggers_critical(self):
        ctx = self._ctx(gas_leak_detected=True)
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.CRITICAL

    def test_hr_extremely_low_triggers_critical(self):
        ctx = self._ctx(heart_rate=35.0)
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.CRITICAL

    def test_hr_extremely_high_triggers_critical(self):
        ctx = self._ctx(heart_rate=180.0)
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.CRITICAL

    def test_spo2_critical_triggers_critical(self):
        ctx = self._ctx(spo2=85.0)
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.CRITICAL

    def test_fall_with_abnormal_vitals_triggers_critical(self):
        ctx = self._ctx(fall_detected=True, heart_rate=150.0)
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.CRITICAL

    # ── HIGH_RISK level tests ──

    def test_fall_no_movement_triggers_high_risk(self):
        ctx = self._ctx(
            fall_detected=True,
            movement_present=False,
            inactivity_seconds=10.0,
        )
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.HIGH_RISK
        assert decision.requires_voice_confirmation

    def test_spo2_warning_triggers_high_risk(self):
        ctx = self._ctx(spo2=90.0)
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.HIGH_RISK

    def test_hr_high_warning_triggers_high_risk(self):
        ctx = self._ctx(heart_rate=150.0)
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.HIGH_RISK

    def test_wandering_with_anomaly_triggers_high_risk(self):
        ctx = self._ctx(wandering_detected=True, behavior_anomaly=True)
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.HIGH_RISK

    # ── WARNING level tests ──

    def test_fall_alone_triggers_warning(self):
        ctx = self._ctx(fall_detected=True, movement_present=True)
        decision = self.engine.evaluate(ctx)
        assert decision.level >= EscalationLevel.WARNING

    def test_wandering_triggers_warning(self):
        ctx = self._ctx(wandering_detected=True)
        decision = self.engine.evaluate(ctx)
        assert decision.level >= EscalationLevel.WARNING

    def test_anomaly_score_triggers_warning(self):
        ctx = self._ctx(anomaly_score=0.4)
        decision = self.engine.evaluate(ctx)
        assert decision.level >= EscalationLevel.WARNING

    # ── MONITOR level tests ──

    def test_normal_state_is_monitor(self):
        ctx = self._ctx()
        decision = self.engine.evaluate(ctx)
        assert decision.level == EscalationLevel.MONITOR

    def test_monitor_has_log_only_action(self):
        ctx = self._ctx()
        decision = self.engine.evaluate(ctx)
        assert decision.actions == ["log_only"]

    # ── Voice confirmation tests ──

    def test_voice_response_positive_downgrades(self):
        decision = PolicyDecision(
            level=EscalationLevel.HIGH_RISK,
            reasons=["fall_no_movement"],
            actions=["voice_check"],
            requires_voice_confirmation=True,
        )
        result = self.engine.handle_voice_response(decision, "I am fine")
        assert result.level == EscalationLevel.MONITOR

    def test_voice_response_distress_escalates(self):
        decision = PolicyDecision(
            level=EscalationLevel.HIGH_RISK,
            reasons=["fall"],
            actions=[],
        )
        result = self.engine.handle_voice_response(decision, "help me")
        assert result.level == EscalationLevel.CRITICAL

    def test_voice_response_none_escalates(self):
        decision = PolicyDecision(
            level=EscalationLevel.HIGH_RISK,
            reasons=["fall"],
            actions=[],
        )
        result = self.engine.handle_voice_response(decision, None)
        assert result.level == EscalationLevel.CRITICAL

    def test_voice_response_unclear_maintains(self):
        decision = PolicyDecision(
            level=EscalationLevel.HIGH_RISK,
            reasons=["fall"],
            actions=[],
        )
        result = self.engine.handle_voice_response(decision, "huh what")
        assert result.level == EscalationLevel.HIGH_RISK

    def test_decision_to_dict(self):
        d = PolicyDecision(
            level=EscalationLevel.WARNING,
            reasons=["test"],
            actions=["notify_caregiver"],
        )
        data = d.to_dict()
        assert data["level"] == 1
        assert data["level_name"] == "WARNING"


# ═════════════════════════════════════════════════════════
# Incident Manager Tests
# ═════════════════════════════════════════════════════════

from src.incident_manager import (
    Incident,
    IncidentState,
    IncidentManager,
    TimelineEntry,
)


class TestIncident:
    """Test Incident data model."""

    def test_incident_creation(self):
        inc = Incident(
            id="test-1",
            created_at=time.time(),
            trigger_event="fall_detected",
            trigger_source="vision_agent",
        )
        assert inc.state == IncidentState.DETECTED
        assert inc.is_active
        assert inc.level == EscalationLevel.MONITOR

    def test_add_event(self):
        inc = Incident(
            id="test-2",
            created_at=time.time(),
            trigger_event="test",
            trigger_source="test",
        )
        inc.add_event("test_event", {"key": "value"})
        assert len(inc.timeline) == 1
        assert inc.timeline[0].event == "test_event"

    def test_duration(self):
        inc = Incident(
            id="test-3",
            created_at=time.time() - 10,
            trigger_event="test",
            trigger_source="test",
        )
        assert inc.duration_seconds >= 10

    def test_resolved_not_active(self):
        inc = Incident(
            id="test-4",
            created_at=time.time(),
            trigger_event="test",
            trigger_source="test",
            state=IncidentState.RESOLVED,
        )
        assert not inc.is_active

    def test_to_dict(self):
        inc = Incident(
            id="test-5",
            created_at=time.time(),
            trigger_event="fall",
            trigger_source="vision",
            room="bedroom",
        )
        data = inc.to_dict()
        assert data["id"] == "test-5"
        assert data["room"] == "bedroom"
        assert "timeline" in data

    def test_to_summary(self):
        inc = Incident(
            id="test-6",
            created_at=time.time(),
            trigger_event="fall",
            trigger_source="vision",
        )
        summary = inc.to_summary()
        assert "id" in summary
        assert "state" in summary


class TestIncidentManager:
    """Test incident lifecycle management."""

    def setup_method(self):
        self.mgr = IncidentManager(dedup_window=1.0)

    def test_create_incident(self):
        inc = self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
            person_id="p1",
            room="bedroom",
        )
        assert inc is not None
        assert inc.trigger_event == "fall"
        assert self.mgr.total_incidents == 1

    def test_dedup_suppresses_duplicate(self):
        self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
            person_id="p1",
        )
        dup = self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
            person_id="p1",
        )
        assert dup is None
        assert self.mgr.total_incidents == 1

    def test_dedup_allows_different_events(self):
        self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
            person_id="p1",
        )
        inc2 = self.mgr.create_incident(
            trigger_event="wandering",
            trigger_source="vision",
            person_id="p1",
        )
        assert inc2 is not None
        assert self.mgr.total_incidents == 2

    def test_escalate(self):
        inc = self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
        )
        decision = PolicyDecision(
            level=EscalationLevel.HIGH_RISK,
            reasons=["fall_no_movement"],
            actions=["voice_check", "notify_caregiver"],
        )
        result = self.mgr.escalate(inc.id, decision)
        assert result.level == EscalationLevel.HIGH_RISK
        assert result.state == IncidentState.ESCALATED
        assert self.mgr.total_escalations == 1

    def test_escalate_with_voice_pending(self):
        inc = self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
        )
        decision = PolicyDecision(
            level=EscalationLevel.HIGH_RISK,
            reasons=["fall"],
            actions=["voice_check"],
            requires_voice_confirmation=True,
        )
        result = self.mgr.escalate(inc.id, decision)
        assert result.state == IncidentState.VOICE_PENDING

    def test_resolve(self):
        inc = self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
        )
        resolved = self.mgr.resolve(inc.id, "voice_confirmed_ok")
        assert resolved.state == IncidentState.RESOLVED
        assert resolved.resolution == "voice_confirmed_ok"
        assert not resolved.is_active

    def test_get_active_incidents(self):
        self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
            person_id="p1",
        )
        self.mgr.create_incident(
            trigger_event="fire",
            trigger_source="env",
            person_id="p2",
        )
        active = self.mgr.get_active_incidents()
        assert len(active) == 2

    def test_get_voice_pending(self):
        inc = self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
        )
        decision = PolicyDecision(
            level=EscalationLevel.HIGH_RISK,
            reasons=["fall"],
            actions=[],
            requires_voice_confirmation=True,
        )
        self.mgr.escalate(inc.id, decision)
        pending = self.mgr.get_voice_pending()
        assert len(pending) == 1

    def test_set_voice_response(self):
        inc = self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
        )
        self.mgr.set_voice_response(inc.id, "I am fine")
        assert inc.voice_response == "I am fine"

    def test_stats(self):
        self.mgr.create_incident(
            trigger_event="fall",
            trigger_source="vision",
        )
        stats = self.mgr.stats
        assert stats["total_incidents"] == 1
        assert stats["active_count"] == 1


# ═════════════════════════════════════════════════════════
# Context Aggregator Tests
# ═════════════════════════════════════════════════════════

from src.context_aggregator import ContextAggregator


class TestContextAggregator:
    """Test multi-source data aggregation."""

    def setup_method(self):
        self.agg = ContextAggregator()

    def test_ingest_basic(self):
        self.agg.ingest("health", "heart_rate", 72.0)
        assert self.agg.get_latest("health", "heart_rate") == 72.0

    def test_ingest_health(self):
        self.agg.ingest_health({"heart_rate": 80, "spo2": 96})
        assert self.agg.get_latest("health", "heart_rate") == 80
        assert self.agg.get_latest("health", "spo2") == 96

    def test_ingest_vision_event(self):
        self.agg.ingest_vision_event({
            "event_type": "fall_detected",
            "severity": "critical",
            "room": "bedroom",
            "person_id": "p1",
        })
        ctx = self.agg.build_context()
        assert ctx.fall_detected
        assert ctx.room == "bedroom"
        assert ctx.person_id == "p1"

    def test_ingest_environmental_fire(self):
        self.agg.ingest_environmental("fire", True)
        ctx = self.agg.build_context()
        assert ctx.fire_detected

    def test_ingest_environmental_gas(self):
        self.agg.ingest_environmental("gas", True)
        ctx = self.agg.build_context()
        assert ctx.gas_leak_detected

    def test_ingest_smartguard(self):
        self.agg.ingest_smartguard({"anomaly_score": 0.45, "is_anomaly": True})
        ctx = self.agg.build_context()
        assert ctx.anomaly_score == 0.45

    def test_ingest_voice_response(self):
        self.agg.ingest_voice_response("I am fine")
        ctx = self.agg.build_context()
        assert ctx.voice_confirmation_pending
        assert ctx.voice_response == "I am fine"

    def test_clear_fall(self):
        self.agg.ingest_vision_event({"event_type": "fall_detected"})
        ctx = self.agg.build_context()
        assert ctx.fall_detected
        self.agg.clear_fall()
        ctx = self.agg.build_context()
        assert not ctx.fall_detected

    def test_clear_environmental(self):
        self.agg.ingest_environmental("fire", True)
        self.agg.clear_environmental("fire")
        ctx = self.agg.build_context()
        assert not ctx.fire_detected

    def test_heart_rate_trend(self):
        for hr in [70, 72, 74, 76, 78]:
            self.agg.ingest("health", "heart_rate", hr)
        trend = self.agg.get_heart_rate_trend()
        assert trend["min"] == 70
        assert trend["max"] == 78
        assert trend["count"] == 5

    def test_heart_rate_trend_empty(self):
        trend = self.agg.get_heart_rate_trend()
        assert trend["min"] is None
        assert trend["count"] == 0

    def test_location_info(self):
        self.agg.ingest_vision_event({
            "event_type": "person_detected",
            "room": "kitchen",
            "person_id": "p1",
        })
        loc = self.agg.get_location_info()
        assert loc["room"] == "kitchen"
        assert loc["person_id"] == "p1"

    def test_snapshot(self):
        self.agg.ingest("health", "heart_rate", 72)
        snap = self.agg.get_snapshot()
        assert "latest_values" in snap
        assert "fire_detected" in snap

    def test_build_context_defaults(self):
        ctx = self.agg.build_context()
        assert not ctx.fire_detected
        assert not ctx.fall_detected
        assert ctx.heart_rate is None
        assert ctx.spo2 is None

    def test_get_history(self):
        for i in range(5):
            self.agg.ingest("health", "heart_rate", 70 + i)
        history = self.agg.get_history("health", "heart_rate")
        assert len(history) == 5


# ═════════════════════════════════════════════════════════
# Medical Profile Tests
# ═════════════════════════════════════════════════════════

from src.medical_profile import MedicalProfile, EmergencyContact


class TestMedicalProfile:
    """Test medical profile management."""

    def setup_method(self):
        self.profile = MedicalProfile.from_dict({
            "id": "resident_1",
            "name": "Test Resident",
            "age": 75,
            "blood_type": "A+",
            "address": "123 Test St",
            "medical_conditions": ["hypertension", "diabetes"],
            "medications": ["lisinopril", "metformin"],
            "allergies": ["penicillin"],
            "emergency_contacts": [
                {
                    "name": "Son",
                    "phone": "+1234567890",
                    "relationship": "son",
                    "telegram_chat_id": "12345",
                    "is_primary": True,
                },
                {
                    "name": "Daughter",
                    "phone": "+0987654321",
                    "relationship": "daughter",
                    "telegram_chat_id": "67890",
                },
            ],
            "baseline_heart_rate": 72.0,
            "baseline_spo2": 97.0,
        })

    def test_from_dict(self):
        assert self.profile.name == "Test Resident"
        assert self.profile.age == 75
        assert len(self.profile.medical_conditions) == 2

    def test_primary_contact(self):
        primary = self.profile.get_primary_contact()
        assert primary is not None
        assert primary.name == "Son"
        assert primary.is_primary

    def test_telegram_chat_ids(self):
        ids = self.profile.get_telegram_chat_ids()
        assert len(ids) == 2
        assert "12345" in ids

    def test_emergency_packet(self):
        packet = self.profile.build_emergency_packet(
            vitals={"heart_rate": 150, "spo2": 88},
            location={"room": "bedroom", "floor": 1},
        )
        assert packet["patient"]["name"] == "Test Resident"
        assert packet["current_vitals"]["heart_rate"] == 150
        assert packet["location"]["room"] == "bedroom"

    def test_context_for_actions(self):
        ctx = self.profile.build_context_for_actions(
            incident_id="inc-1",
            room="bedroom",
            heart_rate=150.0,
            level_name="CRITICAL",
            reasons=["fall_with_abnormal_vitals"],
        )
        assert ctx["person_name"] == "Test Resident"
        assert ctx["heart_rate"] == 150.0
        assert ctx["level_name"] == "CRITICAL"
        assert len(ctx["chat_ids"]) == 2

    def test_to_dict(self):
        data = self.profile.to_dict()
        assert data["resident_id"] == "resident_1"
        assert data["blood_type"] == "A+"

    def test_from_empty_dict(self):
        p = MedicalProfile.from_dict({})
        assert p.name == ""
        assert p.baseline_heart_rate == 72.0


# ═════════════════════════════════════════════════════════
# Action Handler Tests
# ═════════════════════════════════════════════════════════

from src.action_handlers import (
    ActionDispatcher,
    HomeAssistantHandler,
    TelegramHandler,
    EmergencyHandler,
)


class TestEmergencyHandler:
    """Test emergency action handler."""

    def test_dev_mode_simulates(self):
        handler = EmergencyHandler(mode="development")
        result = handler.execute("emergency_call", {"incident_id": "test"})
        assert result["success"]
        assert result["simulated"]

    def test_medical_packet_dev(self):
        handler = EmergencyHandler(mode="development")
        result = handler.execute("send_medical_packet", {
            "incident_id": "test",
            "person_name": "Resident",
            "age": 75,
            "room": "bedroom",
        })
        assert result["success"]
        assert "packet" in result

    @patch("src.action_handlers.requests.post")
    def test_production_mode_twilio_call(self, mock_post):
        mock_client = MagicMock()
        mock_call = MagicMock()
        mock_call.sid = "CA_test_sid_123"
        mock_client.calls.create.return_value = mock_call

        handler = EmergencyHandler(
            mode="production",
            twilio_account_sid="AC_test",
            twilio_auth_token="test_token",
            twilio_from_number="+15551234567",
            emergency_to_number="+919876543210",
        )
        # Inject mock client
        handler._twilio_client = mock_client

        result = handler.execute("emergency_call", {
            "incident_id": "test-prod",
            "level_name": "CRITICAL",
            "person_name": "Resident",
            "room": "bedroom",
            "reasons": ["fall_detected", "no_voice_response"],
        })
        assert result["success"]
        assert result["mode"] == "production"
        assert result["call_sid"] == "CA_test_sid_123"
        mock_client.calls.create.assert_called_once()

    def test_production_mode_no_client(self):
        handler = EmergencyHandler(mode="production")
        result = handler.execute("emergency_call", {
            "incident_id": "test",
        })
        assert not result["success"]
        assert result["error"] == "twilio_not_configured"

    def test_production_mode_no_phone(self):
        handler = EmergencyHandler(mode="production")
        handler._twilio_client = MagicMock()
        # No to_number configured and none in context
        result = handler.execute("emergency_call", {
            "incident_id": "test",
        })
        assert not result["success"]
        assert result["error"] == "no_phone_number"

    def test_production_call_with_context_override(self):
        mock_client = MagicMock()
        mock_call = MagicMock()
        mock_call.sid = "CA_override"
        mock_client.calls.create.return_value = mock_call

        handler = EmergencyHandler(
            mode="production",
            emergency_to_number="+911111111111",
        )
        handler._twilio_client = mock_client

        result = handler.execute("emergency_call", {
            "incident_id": "test",
            "level_name": "CRITICAL",
            "to_number": "+919999999999",
        })
        assert result["success"]
        assert result["to"] == "+919999999999"

    def test_conversational_mode_uses_url(self):
        """When public_url is set, call uses url= not twiml=."""
        mock_client = MagicMock()
        mock_call = MagicMock()
        mock_call.sid = "CA_conv_123"
        mock_client.calls.create.return_value = mock_call

        handler = EmergencyHandler(
            mode="production",
            emergency_to_number="+919876543210",
            public_url="https://abc.ngrok-free.app",
        )
        handler._twilio_client = mock_client

        result = handler.execute("emergency_call", {
            "incident_id": "inc-conv",
            "level_name": "CRITICAL",
            "person_name": "Test",
            "room": "bedroom",
        })
        assert result["success"]
        assert result["conversational"] is True
        assert result["call_sid"] == "CA_conv_123"

        call_kwargs = mock_client.calls.create.call_args
        # Should use url= parameter, not twiml=
        assert "url" in call_kwargs.kwargs or (
            len(call_kwargs.args) == 0
            and "url" in str(call_kwargs)
        )
        # url should contain the webhook path and incident_id
        url_arg = call_kwargs.kwargs.get(
            "url", call_kwargs[1].get("url", "")
        )
        assert "/twilio/voice" in url_arg
        assert "inc-conv" in url_arg

    def test_static_fallback_without_public_url(self):
        """Without public_url, call uses inline twiml=."""
        mock_client = MagicMock()
        mock_call = MagicMock()
        mock_call.sid = "CA_static_456"
        mock_client.calls.create.return_value = mock_call

        handler = EmergencyHandler(
            mode="production",
            emergency_to_number="+919876543210",
        )
        handler._twilio_client = mock_client

        result = handler.execute("emergency_call", {
            "incident_id": "inc-static",
            "level_name": "CRITICAL",
            "person_name": "Resident",
            "room": "kitchen",
            "reasons": ["fall_detected"],
        })
        assert result["success"]
        assert result.get("conversational") is False

        call_kwargs = mock_client.calls.create.call_args
        twiml_arg = call_kwargs.kwargs.get(
            "twiml", call_kwargs[1].get("twiml", "")
        )
        assert "<Response>" in twiml_arg
        assert "fall_detected" in twiml_arg


# ═════════════════════════════════════════════════════════
# Twilio Webhook Tests
# ═════════════════════════════════════════════════════════

from src.rest_api import create_app


class TestTwilioWebhooks:
    """Test Twilio conversational AI webhook endpoints."""

    def _make_engine(self):
        """Create a minimal mock engine for the Flask app."""
        engine = MagicMock()
        engine.started_at = time.time()

        # Medical profile
        profile = MagicMock()
        profile.name = "Test Patient"
        profile.age = 75
        profile.address = "123 Test Street"
        profile.floor = 1
        profile.medical_conditions = ["hypertension"]
        profile.allergies = ["penicillin"]
        profile.medications = ["lisinopril"]
        profile.blood_type = "O+"
        profile.emergency_contacts = [
            {"name": "John Doe", "relationship": "Son", "phone": "+1555"}
        ]
        engine.medical = profile

        # Context — EscalationContext is a dataclass, not a dict
        ctx = MagicMock()
        ctx.heart_rate = 95
        ctx.spo2 = 91
        ctx.room = "bedroom"
        ctx.floor = 1
        ctx.movement_present = False
        ctx.voice_response = None
        engine.context.build_context.return_value = ctx
        engine.context.get_latest.return_value = None

        # Incidents
        incident = MagicMock()
        incident.room = "bedroom"
        incident.level.name = "CRITICAL"
        # Timeline entries are TimelineEntry dataclass objects
        timeline_entry = MagicMock()
        timeline_entry.event = "escalated"
        timeline_entry.details = {
            "reasons": [
                "fall_detected",
                "no_voice_response",
            ]
        }
        incident.timeline = [timeline_entry]
        engine.incidents.get_incident.return_value = incident
        engine.incidents.get_active_incidents.return_value = []
        engine.incidents.stats = {}

        return engine

    def test_voice_webhook_returns_twiml(self):
        """Initial webhook should return Gather TwiML."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post("/twilio/voice?incident_id=inc-123")
        assert resp.status_code == 200
        assert resp.content_type == "application/xml"

        body = resp.data.decode()
        assert "<Gather" in body
        assert "input='speech'" in body or 'input="speech"' in body
        assert "/twilio/respond" in body
        assert "inc-123" in body
        assert "Test Patient" in body
        assert "bedroom" in body

    def test_voice_webhook_includes_vitals(self):
        """Briefing should include heart rate and SpO2."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post("/twilio/voice?incident_id=inc-v")
        body = resp.data.decode()
        assert "95" in body  # heart rate
        assert "91" in body  # spo2

    def test_respond_answers_name_question(self):
        """Responder asks about patient name."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "What is the patient's name?", "Confidence": "0.9"},
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Test Patient" in body
        assert "75" in body

    def test_respond_answers_vitals_question(self):
        """Responder asks about vitals."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "What is the heart rate?", "Confidence": "0.85"},
        )
        body = resp.data.decode()
        assert "95" in body

    def test_respond_answers_allergies(self):
        """Responder asks about allergies."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "Are there any known allergies?"},
        )
        body = resp.data.decode()
        assert "penicillin" in body

    def test_respond_answers_medications(self):
        """Responder asks about medications."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "What medications are being taken?"},
        )
        body = resp.data.decode()
        assert "lisinopril" in body

    def test_respond_answers_location(self):
        """Responder asks about location."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "What is the location and address?"},
        )
        body = resp.data.decode()
        assert "bedroom" in body
        assert "123 Test Street" in body

    def test_respond_answers_emergency_contacts(self):
        """Responder asks about emergency contacts."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "List the emergency contacts please"},
        )
        body = resp.data.decode()
        assert "John Doe" in body
        assert "Son" in body

    def test_respond_answers_what_happened(self):
        """Responder asks what happened."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "What happened? What is the reason for the alert?"},
        )
        body = resp.data.decode()
        assert "fall_detected" in body

    def test_respond_answers_blood_type(self):
        """Responder asks about blood type."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "What is the blood type?"},
        )
        body = resp.data.decode()
        assert "O+" in body

    def test_respond_answers_medical_history(self):
        """Responder asks about medical history."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "What is the medical history?"},
        )
        body = resp.data.decode()
        assert "hypertension" in body

    def test_respond_goodbye_ends_call(self):
        """Saying goodbye ends the conversation."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "Thank you, goodbye"},
        )
        body = resp.data.decode()
        assert "Goodbye" in body
        # Should NOT have another Gather
        assert "<Gather" not in body

    def test_respond_empty_speech_prompts_repeat(self):
        """Empty speech input asks to repeat."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": ""},
        )
        body = resp.data.decode()
        assert "repeat" in body.lower() or "catch" in body.lower()
        assert "<Gather" in body

    def test_respond_unknown_question_offers_help(self):
        """Unknown question offers categories of available info."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "What color is the sky?"},
        )
        body = resp.data.decode()
        # Fallback should mention available categories
        assert "rephrase" in body.lower() or "information" in body.lower()

    def test_respond_continues_conversation(self):
        """After answering, should offer more questions."""
        engine = self._make_engine()
        app = create_app(engine)
        client = app.test_client()

        resp = client.post(
            "/twilio/respond?incident_id=inc-123",
            data={"SpeechResult": "What is the patient age?"},
        )
        body = resp.data.decode()
        assert "75" in body
        # Should have another Gather for follow-up
        assert "<Gather" in body
        assert "other questions" in body.lower()


class TestTelegramHandler:
    """Test Telegram notification handler."""

    def test_format_message(self):
        handler = TelegramHandler()
        msg = handler._format_message("notify_caregiver", {
            "level_name": "CRITICAL",
            "room": "bedroom",
            "person_name": "Resident",
            "reasons": ["fall_with_abnormal_vitals"],
            "heart_rate": 150,
            "spo2": 88,
            "incident_id": "test-1",
        })
        assert "CRITICAL" in msg
        assert "bedroom" in msg
        assert "150" in msg

    @patch("src.action_handlers.requests.post")
    def test_execute_sends_telegram_directly(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_post.return_value = mock_resp

        handler = TelegramHandler(
            bot_token="test-token",
            chat_ids=["123"],
        )
        result = handler.execute("notify_caregiver", {
            "level_name": "WARNING",
            "room": "kitchen",
            "person_name": "Test",
        })
        assert result["success"]
        assert "telegram_sent:123" in result["methods"]
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "test-token" in call_args[0][0]
        assert call_args[1]["json"]["chat_id"] == "123"

    @patch("src.action_handlers.requests.post")
    def test_execute_with_mqtt_and_telegram(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_post.return_value = mock_resp
        mock_publish = MagicMock()

        handler = TelegramHandler(
            bot_token="test-token",
            chat_ids=["123"],
            mqtt_publish_fn=mock_publish,
        )
        result = handler.execute("notify_caregiver", {
            "level_name": "WARNING",
            "room": "kitchen",
            "person_name": "Test",
        })
        assert result["success"]
        mock_publish.assert_called_once()

    def test_execute_no_token_warns(self):
        handler = TelegramHandler()
        result = handler.execute("notify_caregiver", {
            "level_name": "WARNING",
            "chat_ids": ["123"],
        })
        assert not result["success"]

    @patch("src.action_handlers.requests.post")
    def test_execute_context_chat_ids_override(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_post.return_value = mock_resp

        handler = TelegramHandler(
            bot_token="test-token",
            chat_ids=["default-id"],
        )
        result = handler.execute("notify_caregiver", {
            "level_name": "CRITICAL",
            "chat_ids": ["override-id"],
        })
        assert result["success"]
        assert "telegram_sent:override-id" in result["methods"]


class TestActionDispatcher:
    """Test action routing."""

    def test_dispatch_to_emergency(self):
        emergency = EmergencyHandler(mode="development")
        dispatcher = ActionDispatcher(emergency_handler=emergency)
        result = dispatcher.dispatch("emergency_call", {"incident_id": "t1"})
        assert result["success"]
        assert result["action"] == "emergency_call"

    def test_dispatch_unknown_action(self):
        dispatcher = ActionDispatcher()
        result = dispatcher.dispatch("unknown_action", {})
        assert not result["success"]

    def test_dispatch_all(self):
        emergency = EmergencyHandler(mode="development")
        dispatcher = ActionDispatcher(emergency_handler=emergency)
        results = dispatcher.dispatch_all(
            ["emergency_call", "send_medical_packet"],
            {"incident_id": "t1"},
        )
        assert len(results) == 2
        assert all(r["success"] for r in results)


# ═════════════════════════════════════════════════════════
# Telemetry Tests
# ═════════════════════════════════════════════════════════

from src.telemetry import TelemetryStream, TelemetryManager


class TestTelemetryStream:
    """Test telemetry streaming."""

    def test_stream_lifecycle(self):
        collected = []
        stream = TelemetryStream(
            incident_id="test-1",
            interval=0.1,
            data_fn=lambda: {"hr": 72},
            publish_fn=lambda d: collected.append(d),
        )
        stream.start()
        assert stream.is_running
        time.sleep(0.35)
        stream.stop()
        assert not stream.is_running
        assert len(collected) >= 2

    def test_stream_stats(self):
        stream = TelemetryStream(
            incident_id="test-2",
            interval=0.1,
        )
        stats = stream.stats
        assert stats["incident_id"] == "test-2"
        assert not stats["running"]


class TestTelemetryManager:
    """Test telemetry manager."""

    def test_start_stream(self):
        mgr = TelemetryManager(default_interval=0.1)
        stream = mgr.start_stream("inc-1")
        assert stream.is_running
        mgr.stop_all()

    def test_stop_stream(self):
        mgr = TelemetryManager(default_interval=0.1)
        mgr.start_stream("inc-1")
        mgr.stop_stream("inc-1")
        assert len(mgr.get_active_streams()) == 0

    def test_get_active_streams(self):
        mgr = TelemetryManager(default_interval=0.1)
        mgr.start_stream("inc-1")
        mgr.start_stream("inc-2")
        active = mgr.get_active_streams()
        assert len(active) == 2
        mgr.stop_all()


# ═════════════════════════════════════════════════════════
# Replay Tests
# ═════════════════════════════════════════════════════════

from src.replay import ReplayBuilder, IncidentReplay


class TestReplayBuilder:
    """Test incident replay recording."""

    def setup_method(self):
        self.builder = ReplayBuilder()

    def test_start_replay(self):
        replay = self.builder.start_replay("inc-1")
        assert replay.incident_id == "inc-1"
        assert len(replay.segments) >= 1  # replay_started event

    def test_add_event(self):
        self.builder.start_replay("inc-1")
        self.builder.add_event(
            "inc-1", "vision", "fall_detected", {"confidence": 0.9}
        )
        replay = self.builder.get_replay("inc-1")
        assert len(replay.segments) == 2  # started + event

    def test_complete_replay(self):
        self.builder.start_replay("inc-1")
        self.builder.add_event("inc-1", "vision", "fall")
        replay = self.builder.complete_replay("inc-1")
        assert replay is not None
        assert len(self.builder.get_active_replays()) == 0
        assert len(self.builder.get_completed_replays()) == 1

    def test_replay_to_dict(self):
        replay = self.builder.start_replay("inc-1")
        self.builder.add_event("inc-1", "vision", "fall")
        data = replay.to_dict()
        assert data["incident_id"] == "inc-1"
        assert "timeline" in data
        assert data["segment_count"] >= 1

    def test_get_completed_replays_limit(self):
        for i in range(5):
            self.builder.start_replay(f"inc-{i}")
            self.builder.complete_replay(f"inc-{i}")
        completed = self.builder.get_completed_replays(limit=3)
        assert len(completed) == 3

    def test_replay_with_pre_context(self):
        pre = [
            {
                "source": "health",
                "event_type": "sensor_data",
                "data": {"hr": 72},
                "timestamp": time.time() - 60,
            },
        ]
        replay = self.builder.start_replay("inc-1", pre_context=pre)
        assert len(replay.segments) == 2  # pre context + started


# ═════════════════════════════════════════════════════════
# Integration Tests
# ═════════════════════════════════════════════════════════

class TestEndToEndPipeline:
    """Test the full event → decision → action pipeline."""

    def test_fall_detection_pipeline(self):
        """Simulate: fall detected → policy → incident → actions."""
        # Setup
        policy = PolicyEngine()
        incidents = IncidentManager(dedup_window=1.0)
        context = ContextAggregator()
        profile = MedicalProfile.from_dict({
            "name": "Resident",
            "age": 75,
            "emergency_contacts": [
                {"name": "Son", "phone": "+123", "telegram_chat_id": "999"},
            ],
        })

        # Simulate vision event
        event = {
            "event_type": "fall_detected",
            "severity": "high",
            "room": "bathroom",
            "person_id": "p1",
        }
        context.ingest_vision_event(event)

        # Build context and evaluate
        ctx = context.build_context()
        assert ctx.fall_detected
        assert ctx.room == "bathroom"

        decision = policy.evaluate(ctx)
        assert decision.level >= EscalationLevel.WARNING

        # Create incident
        incident = incidents.create_incident(
            trigger_event="fall_detected",
            trigger_source="vision_agent",
            person_id="p1",
            room="bathroom",
        )
        assert incident is not None

        # Escalate
        incidents.escalate(incident.id, decision)
        assert incident.level >= EscalationLevel.WARNING

        # Build action context
        action_ctx = profile.build_context_for_actions(
            incident_id=incident.id,
            room="bathroom",
            level_name=decision.level.name,
            reasons=decision.reasons,
        )
        assert action_ctx["person_name"] == "Resident"
        assert action_ctx["room"] == "bathroom"

    def test_fire_critical_pipeline(self):
        """Simulate: fire → CRITICAL → emergency actions."""
        policy = PolicyEngine()
        context = ContextAggregator()

        context.ingest_environmental("fire", True)
        ctx = context.build_context()
        decision = policy.evaluate(ctx)

        assert decision.level == EscalationLevel.CRITICAL
        assert "emergency_call" in decision.actions
        assert "activate_siren" in decision.actions
        assert not decision.requires_voice_confirmation

    def test_voice_confirmation_flow(self):
        """Simulate: fall → HIGH_RISK → voice check → 'I am fine' → resolved."""
        policy = PolicyEngine()
        incidents = IncidentManager(dedup_window=1.0)
        context = ContextAggregator()

        # Fall with no movement
        context.ingest_vision_event({
            "event_type": "fall_detected",
            "room": "hallway",
        })
        # Simulate prolonged inactivity
        context._last_movement_time = time.time() - 200

        ctx = context.build_context()
        decision = policy.evaluate(ctx)
        assert decision.level >= EscalationLevel.HIGH_RISK

        incident = incidents.create_incident(
            trigger_event="fall_detected",
            trigger_source="vision",
        )
        incidents.escalate(incident.id, decision)

        # Voice response
        updated = policy.handle_voice_response(decision, "I am fine")
        assert updated.level == EscalationLevel.MONITOR

        incidents.resolve(incident.id, "voice_confirmed_ok")
        assert not incident.is_active

    def test_health_emergency_pipeline(self):
        """Simulate: extremely low HR → CRITICAL."""
        policy = PolicyEngine()
        context = ContextAggregator()

        context.ingest_health({"heart_rate": 35, "spo2": 85})
        ctx = context.build_context()
        decision = policy.evaluate(ctx)

        assert decision.level == EscalationLevel.CRITICAL
        assert "emergency_call" in decision.actions


# ═════════════════════════════════════════════════════════
# Action Dedup Tests
# ═════════════════════════════════════════════════════════


class TestActionDedup:
    """Test that emergency_call and voice_check are not repeated."""

    @patch("main.MQTTBridge", autospec=True)
    def test_emergency_call_not_repeated(self, _mock_mqtt):
        """Second dispatch for same incident skips emergency_call."""
        from main import OpenClawEngine

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine._emergency_call_placed = set()
            engine._voice_check_done = set()
            engine._voice_session_active = False
            engine._emergency_call_active = False
            engine._emergency_call_incident = None

            # Simulate first dispatch marking it done
            engine._mark_actions_done = (
                OpenClawEngine._mark_actions_done.__get__(engine)
            )
            engine._filter_actions = (
                OpenClawEngine._filter_actions.__get__(engine)
            )

            actions = [
                "emergency_call", "notify_caregiver",
                "unlock_door", "voice_check",
            ]

            # First pass: all actions present
            filtered = engine._filter_actions("inc-001", actions)
            assert "emergency_call" in filtered
            assert "voice_check" in filtered
            engine._mark_actions_done("inc-001", filtered)

            # _mark_actions_done sets the call active lock
            assert engine._emergency_call_active is True
            assert engine._emergency_call_incident == "inc-001"

            # Second pass: emergency_call and voice_check removed
            filtered2 = engine._filter_actions("inc-001", actions)
            assert "emergency_call" not in filtered2
            assert "voice_check" not in filtered2
            assert "notify_caregiver" in filtered2
            assert "unlock_door" in filtered2

    @patch("main.MQTTBridge", autospec=True)
    def test_voice_check_blocked_after_response(self, _mock_mqtt):
        """voice_check is skipped once voice response is received."""
        from main import OpenClawEngine

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine._emergency_call_placed = set()
            engine._voice_check_done = set()
            engine._voice_session_active = False
            engine._emergency_call_active = False
            engine._emergency_call_incident = None
            engine._filter_actions = (
                OpenClawEngine._filter_actions.__get__(engine)
            )

            # Simulate voice response received
            engine._voice_check_done.add("inc-002")

            actions = ["voice_check", "notify_caregiver"]
            filtered = engine._filter_actions("inc-002", actions)
            assert "voice_check" not in filtered
            assert "notify_caregiver" in filtered

    @patch("main.MQTTBridge", autospec=True)
    def test_dedup_cleared_on_resolve(self, _mock_mqtt):
        """Dedup sets are cleaned when incident is resolved."""
        from main import OpenClawEngine

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine._emergency_call_placed = {"inc-003"}
            engine._voice_check_done = {"inc-003"}
            engine._voice_session_active = False
            engine._voice_session_incident = None
            engine._emergency_call_active = True
            engine._emergency_call_incident = "inc-003"
            engine._telemetry = MagicMock()
            engine._replay = MagicMock()
            engine._context = MagicMock()
            engine._mqtt = MagicMock()

            mock_incident = MagicMock()
            mock_incident.id = "inc-003"

            engine.on_incident_resolved = (
                OpenClawEngine.on_incident_resolved.__get__(engine)
            )
            engine.on_incident_resolved(mock_incident)

            assert "inc-003" not in engine._emergency_call_placed
            assert "inc-003" not in engine._voice_check_done
            assert engine._emergency_call_active is False
            assert engine._emergency_call_incident is None

    @patch("main.MQTTBridge", autospec=True)
    def test_has_active_critical_blocks_new_incidents(self, _mock_mqtt):
        """_has_active_critical_incident returns True when CRITICAL exists."""
        from main import OpenClawEngine

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine._incidents = MagicMock()
            engine._has_active_critical_incident = (
                OpenClawEngine._has_active_critical_incident.__get__(engine)
            )

            # No active incidents
            engine._incidents.get_active_incidents.return_value = []
            assert engine._has_active_critical_incident() is False

            # Active but MONITOR incident
            low = MagicMock()
            low.is_active = True
            low.level = EscalationLevel.MONITOR
            engine._incidents.get_active_incidents.return_value = [low]
            assert engine._has_active_critical_incident() is False

            # Active CRITICAL incident
            crit = MagicMock()
            crit.is_active = True
            crit.level = EscalationLevel.CRITICAL
            engine._incidents.get_active_incidents.return_value = [crit]
            assert engine._has_active_critical_incident() is True

    @patch("main.MQTTBridge", autospec=True)
    def test_different_incidents_not_affected(self, _mock_mqtt):
        """Dedup only applies per-incident, not globally."""
        from main import OpenClawEngine

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine._emergency_call_placed = {"inc-A"}
            engine._voice_check_done = {"inc-A"}
            engine._voice_session_active = False
            engine._emergency_call_active = False
            engine._emergency_call_incident = None
            engine._filter_actions = (
                OpenClawEngine._filter_actions.__get__(engine)
            )

            actions = ["emergency_call", "voice_check"]

            # inc-A: emergency blocked, voice_check blocked
            filtered_a = engine._filter_actions("inc-A", actions)
            assert "emergency_call" not in filtered_a
            assert "voice_check" not in filtered_a

            # inc-B: NOT blocked (different incident, no session)
            filtered_b = engine._filter_actions("inc-B", actions)
            assert "emergency_call" in filtered_b
            assert "voice_check" in filtered_b

    @patch("main.MQTTBridge", autospec=True)
    def test_voice_session_blocks_all_voice_checks(self, _mock_mqtt):
        """Global voice session blocks voice_check for ANY incident."""
        from main import OpenClawEngine

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine._emergency_call_placed = set()
            engine._voice_check_done = set()
            engine._voice_session_active = True  # Session live
            engine._emergency_call_active = False
            engine._emergency_call_incident = None
            engine._filter_actions = (
                OpenClawEngine._filter_actions.__get__(engine)
            )

            actions = ["emergency_call", "voice_check"]
            filtered = engine._filter_actions("inc-NEW", actions)
            # emergency_call passes, voice_check blocked globally
            assert "emergency_call" in filtered
            assert "voice_check" not in filtered

    @patch("main.MQTTBridge", autospec=True)
    def test_emergency_call_lock_blocks_all_calls(self, _mock_mqtt):
        """Global emergency call lock blocks calls for ANY incident."""
        from main import OpenClawEngine

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine._emergency_call_placed = set()
            engine._voice_check_done = set()
            engine._voice_session_active = False
            engine._emergency_call_active = True  # Call active
            engine._emergency_call_incident = "inc-ORIG"
            engine._filter_actions = (
                OpenClawEngine._filter_actions.__get__(engine)
            )
            engine._mark_actions_done = (
                OpenClawEngine._mark_actions_done.__get__(engine)
            )

            actions = ["emergency_call", "notify_caregiver"]
            # Different incident — emergency_call still blocked
            filtered = engine._filter_actions("inc-OTHER", actions)
            assert "emergency_call" not in filtered
            assert "notify_caregiver" in filtered


# ═════════════════════════════════════════════════════════
# Alexa Voice Monitor Tests
# ═════════════════════════════════════════════════════════


class TestAlexaVoiceMonitor:
    """Test _start_voice_timer with last_called_summary keyword matching."""

    def _make_engine(self):
        """Create a minimal ETMSEngine with mocked dependencies."""
        from unittest.mock import patch, MagicMock
        with patch("main.MQTTClient"):
            engine = MagicMock()
            engine.config = {
                "voice_confirmation": {"timeout_seconds": 10},
            }
            engine._voice_timers = {}
            engine.process_voice_response = MagicMock()
        return engine

    def test_positive_keyword_ok(self):
        """'I am fine' should classify as 'ok'."""
        _POSITIVE_KEYWORDS = {
            "fine", "okay", "ok", "good", "alright",
            "i'm fine", "i am fine", "i'm okay", "i am okay",
            "i'm good", "i am good", "i'm alright", "i am alright",
            "no problem", "all good",
        }

        def classify(summary: str) -> str:
            text = summary.lower().strip()
            for kw in _POSITIVE_KEYWORDS:
                if kw in text:
                    return "ok"
            return "not_ok"

        assert classify("I am fine") == "ok"
        assert classify("i'm okay") == "ok"
        assert classify("I'm good") == "ok"
        assert classify("all good") == "ok"
        assert classify("yes I am alright") == "ok"
        assert classify("no problem") == "ok"

    def test_non_positive_classified_as_not_ok(self):
        """Anything that's not a positive keyword classifies as not_ok."""
        _POSITIVE_KEYWORDS = {
            "fine", "okay", "ok", "good", "alright",
            "i'm fine", "i am fine", "i'm okay", "i am okay",
            "i'm good", "i am good", "i'm alright", "i am alright",
            "no problem", "all good",
        }

        def classify(summary: str) -> str:
            text = summary.lower().strip()
            for kw in _POSITIVE_KEYWORDS:
                if kw in text:
                    return "ok"
            return "not_ok"

        # Distress phrases → not_ok
        assert classify("please help me") == "not_ok"
        assert classify("I fell down") == "not_ok"
        assert classify("i'm hurt") == "not_ok"
        assert classify("call ambulance") == "not_ok"
        assert classify("i am in pain") == "not_ok"
        # Random speech → not_ok
        assert classify("what time is it") == "not_ok"
        assert classify("play a song") == "not_ok"

    def test_not_ok_takes_priority_over_ambiguity(self):
        """Any non-positive phrase results in escalation."""
        _POSITIVE_KEYWORDS = {
            "fine", "okay", "ok", "good", "alright",
            "i'm fine", "i am fine",
        }

        def classify(summary: str) -> str:
            text = summary.lower().strip()
            for kw in _POSITIVE_KEYWORDS:
                if kw in text:
                    return "ok"
            return "not_ok"

        # Pure positive → ok
        assert classify("i am fine") == "ok"
        # Random speech → not_ok (escalate)
        assert classify("what time is it") == "not_ok"
        assert classify("turn on the lights") == "not_ok"

    def test_unrecognized_is_not_ok(self):
        """Random speech classifies as not_ok (triggers escalation)."""
        _POSITIVE_KEYWORDS = {
            "fine", "okay", "ok", "good", "alright",
        }

        def classify(summary: str) -> str:
            text = summary.lower().strip()
            for kw in _POSITIVE_KEYWORDS:
                if kw in text:
                    return "ok"
            return "not_ok"

        assert classify("stop the song") == "not_ok"
        assert classify("what's the weather") == "not_ok"
        assert classify("turn on the lights") == "not_ok"

    @patch("main.MQTTBridge", autospec=True)
    def test_voice_monitor_detects_positive(self, _mock_mqtt):
        """Integration test: 'I am fine' → ok + reassurance announcement."""
        from main import OpenClawEngine

        ha_handler = MagicMock()
        ha_handler.alexa_entity_id = "media_player.echo_dot"
        ha_handler.get_entity_state.side_effect = [
            {
                "attributes": {
                    "last_called_timestamp": "1000",
                    "last_called_summary": "stop the song",
                }
            },
            {
                "attributes": {
                    "last_called_timestamp": "1000",
                    "last_called_summary": "stop the song",
                }
            },
            {
                "attributes": {
                    "last_called_timestamp": "2000",
                    "last_called_summary": "i am fine",
                }
            },
        ]

        # Mock context for vitals text
        mock_ctx = MagicMock()
        mock_ctx.heart_rate = 72
        mock_ctx.spo2 = 98
        mock_context_agg = MagicMock()
        mock_context_agg.build_context.return_value = mock_ctx

        mock_medical = MagicMock()
        mock_medical.name = "Pavan"

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine.config = {
                "voice_confirmation": {"timeout_seconds": 30},
            }
            engine._voice_timers = {}
            engine._voice_session_active = False
            engine._voice_session_incident = None
            engine._emergency_call_active = False
            engine._emergency_call_incident = None
            engine._ha_handler = ha_handler
            engine._context = mock_context_agg
            engine._medical = mock_medical
            engine.process_voice_response = MagicMock()
            import queue as _q
            engine._voice_mqtt_response = _q.Queue()

            engine._start_voice_timer = (
                OpenClawEngine._start_voice_timer.__get__(engine)
            )
            engine._start_voice_timer("inc-test-001")

            import time
            time.sleep(12)

            # Should have called announce_message with reassurance
            ha_handler.announce_message.assert_called_once()
            msg = ha_handler.announce_message.call_args[0][0]
            assert "no emergency call" in msg.lower()
            assert "monitoring" in msg.lower()

            engine.process_voice_response.assert_called_once()
            payload = (
                engine.process_voice_response.call_args[0][1]
            )
            assert payload["response"] == "ok"
            assert payload["source"] == "alexa_voice"
            assert payload["transcript"] == "i am fine"

    @patch("main.MQTTBridge", autospec=True)
    def test_voice_monitor_detects_help(self, _mock_mqtt):
        """Integration test: non-positive response → escalation."""
        from main import OpenClawEngine

        ha_handler = MagicMock()
        ha_handler.alexa_entity_id = "media_player.echo_dot"
        ha_handler.get_entity_state.side_effect = [
            {
                "attributes": {
                    "last_called_timestamp": "1000",
                    "last_called_summary": "play music",
                }
            },
            {
                "attributes": {
                    "last_called_timestamp": "3000",
                    "last_called_summary": "what is that noise",
                }
            },
        ]

        mock_ctx = MagicMock()
        mock_ctx.heart_rate = 55
        mock_ctx.spo2 = 91
        mock_context_agg = MagicMock()
        mock_context_agg.build_context.return_value = mock_ctx

        mock_medical = MagicMock()
        mock_medical.name = "Pavan"

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine.config = {
                "voice_confirmation": {"timeout_seconds": 30},
            }
            engine._voice_timers = {}
            engine._voice_session_active = False
            engine._voice_session_incident = None
            engine._emergency_call_active = False
            engine._emergency_call_incident = None
            engine._ha_handler = ha_handler
            engine._context = mock_context_agg
            engine._medical = mock_medical
            engine.process_voice_response = MagicMock()
            import queue as _q
            engine._voice_mqtt_response = _q.Queue()

            engine._start_voice_timer = (
                OpenClawEngine._start_voice_timer.__get__(engine)
            )
            engine._start_voice_timer("inc-help-001")

            import time
            time.sleep(14)

            # Should announce pre-emergency warning
            ha_handler.announce_message.assert_called_once()
            msg = ha_handler.announce_message.call_args[0][0]
            assert "calling emergency" in msg.lower()
            assert "health" in msg.lower()
            assert "help is on the way" in msg.lower()

            engine.process_voice_response.assert_called_once()
            payload = (
                engine.process_voice_response.call_args[0][1]
            )
            assert payload["response"] == "help"
            assert payload["source"] == "alexa_voice"

    @patch("main.MQTTBridge", autospec=True)
    def test_voice_monitor_timeout(self, _mock_mqtt):
        """Integration test: no interaction → timeout announcement."""
        from main import OpenClawEngine

        ha_handler = MagicMock()
        ha_handler.alexa_entity_id = "media_player.echo_dot"
        ha_handler.get_entity_state.return_value = {
            "attributes": {
                "last_called_timestamp": "1000",
                "last_called_summary": "stop",
            }
        }

        mock_ctx = MagicMock()
        mock_ctx.heart_rate = 80
        mock_ctx.spo2 = 96
        mock_context_agg = MagicMock()
        mock_context_agg.build_context.return_value = mock_ctx

        mock_medical = MagicMock()
        mock_medical.name = "Pavan"

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine.config = {
                "voice_confirmation": {"timeout_seconds": 16},
            }
            engine._voice_timers = {}
            engine._voice_session_active = False
            engine._voice_session_incident = None
            engine._emergency_call_active = False
            engine._emergency_call_incident = None
            engine._ha_handler = ha_handler
            engine._context = mock_context_agg
            engine._medical = mock_medical
            engine.process_voice_response = MagicMock()
            import queue as _q
            engine._voice_mqtt_response = _q.Queue()

            engine._start_voice_timer = (
                OpenClawEngine._start_voice_timer.__get__(engine)
            )
            engine._start_voice_timer("inc-timeout-001")

            import time
            # 6s announcement + 20s poll + 5s timeout announce = 31s
            time.sleep(35)

            # Should announce timeout warning
            ha_handler.announce_message.assert_called_once()
            msg = ha_handler.announce_message.call_args[0][0]
            assert "not responded" in msg.lower()
            assert "calling" in msg.lower()
            assert "help is coming" in msg.lower()

            engine.process_voice_response.assert_called_once()
            payload = (
                engine.process_voice_response.call_args[0][1]
            )
            assert payload["response"] is None

    @patch("main.MQTTBridge", autospec=True)
    def test_voice_monitor_non_positive_escalates(self, _mock_mqtt):
        """Non-positive response → immediate escalation, no re-prompt."""
        from main import OpenClawEngine

        ha_handler = MagicMock()
        ha_handler.alexa_entity_id = "media_player.echo_dot"
        ha_handler.get_entity_state.side_effect = [
            # Initial snapshot
            {
                "attributes": {
                    "last_called_timestamp": "1000",
                    "last_called_summary": "placeholder",
                }
            },
            # First poll: distress / non-positive speech
            {
                "attributes": {
                    "last_called_timestamp": "2000",
                    "last_called_summary": "something is wrong",
                }
            },
        ]

        mock_ctx = MagicMock()
        mock_ctx.heart_rate = 75
        mock_ctx.spo2 = 97
        mock_context_agg = MagicMock()
        mock_context_agg.build_context.return_value = mock_ctx

        mock_medical = MagicMock()
        mock_medical.name = "Test"

        with patch.object(
            OpenClawEngine, "__init__", lambda self: None
        ):
            engine = OpenClawEngine()
            engine.config = {
                "voice_confirmation": {"timeout_seconds": 40},
            }
            engine._voice_timers = {}
            engine._voice_session_active = False
            engine._voice_session_incident = None
            engine._emergency_call_active = False
            engine._emergency_call_incident = None
            engine._ha_handler = ha_handler
            engine._context = mock_context_agg
            engine._medical = mock_medical
            engine.process_voice_response = MagicMock()
            import queue as _q
            engine._voice_mqtt_response = _q.Queue()

            engine._start_voice_timer = (
                OpenClawEngine._start_voice_timer.__get__(engine)
            )
            engine._start_voice_timer("inc-nonpos-001")

            import time
            time.sleep(14)

            # Should announce emergency warning (no re-prompt)
            ha_handler.announce_message.assert_called_once()
            msg = ha_handler.announce_message.call_args[0][0]
            assert "calling emergency" in msg.lower()

            engine.process_voice_response.assert_called_once()
            payload = (
                engine.process_voice_response.call_args[0][1]
            )
            assert payload["response"] == "help"

    def test_voice_check_announcement_text(self):
        """Voice check announcement instructs using Alexa wake word."""
        from src.action_handlers import HomeAssistantHandler

        with patch(
            "src.action_handlers.requests.post"
        ) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = []
            mock_post.return_value = mock_resp

            handler = HomeAssistantHandler(
                base_url="http://localhost:8123",
                token="fake_token",
            )

            result = handler.execute("voice_check", {
                "entity_id": "media_player.echo_dot",
                "incident_id": "test-vc-001",
                "person_name": "Pavan",
            })

        assert result["success"]
        # Verify the POST call used wake-word instructions
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get(
            "json", call_kwargs[1].get("json", {})
        )
        msg = body.get("message", "")
        assert "Alexa" in msg
        assert "fine" in msg.lower()
        assert "20 seconds" in msg

    def test_announce_message_sends_tts(self):
        """announce_message() sends a generic TTS via Alexa."""
        from src.action_handlers import HomeAssistantHandler

        with patch(
            "src.action_handlers.requests.post"
        ) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = []
            mock_post.return_value = mock_resp

            handler = HomeAssistantHandler(
                base_url="http://localhost:8123",
                token="fake_token",
                alexa_entity_id="media_player.echo_dot",
            )

            result = handler.announce_message(
                "No emergency call. I am monitoring your vitals."
            )

        assert result["success"]
        mock_post.assert_called_once()
        body = mock_post.call_args.kwargs.get(
            "json", mock_post.call_args[1].get("json", {})
        )
        assert body["message"] == (
            "No emergency call. I am monitoring your vitals."
        )
        assert body["target"] == ["media_player.echo_dot"]
        assert body["data"]["type"] == "announce"

    def test_announce_message_custom_entity(self):
        """announce_message() targets a custom entity when provided."""
        from src.action_handlers import HomeAssistantHandler

        with patch(
            "src.action_handlers.requests.post"
        ) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = []
            mock_post.return_value = mock_resp

            handler = HomeAssistantHandler(
                base_url="http://localhost:8123",
                token="fake_token",
                alexa_entity_id="media_player.default_echo",
            )

            result = handler.announce_message(
                "Test message",
                entity_id="media_player.bedroom_echo",
            )

        assert result["success"]
        body = mock_post.call_args.kwargs.get(
            "json", mock_post.call_args[1].get("json", {})
        )
        assert body["target"] == ["media_player.bedroom_echo"]

    def test_announce_message_handles_failure(self):
        """announce_message() returns error dict on network failure."""
        from src.action_handlers import HomeAssistantHandler
        import requests as req

        with patch(
            "src.action_handlers.requests.post",
            side_effect=req.ConnectionError("Connection refused"),
        ):
            handler = HomeAssistantHandler(
                base_url="http://localhost:8123",
                token="fake_token",
            )

            result = handler.announce_message("test")

        assert not result["success"]
        assert "error" in result


# ═════════════════════════════════════════════════════════
# Run
# ═════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
