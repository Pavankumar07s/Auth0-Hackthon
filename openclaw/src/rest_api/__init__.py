"""REST API for OpenClaw.

Provides HTTP endpoints for incident management, status queries,
integration with external systems, and Twilio conversational
AI webhooks for interactive emergency calls.

Endpoints:
    GET  /api/status                  → Service health + stats
    GET  /api/incidents/active        → All active incidents
    GET  /api/incidents/recent        → Recent incidents (last 20)
    GET  /api/incident/<id>           → Specific incident details
    GET  /api/incident/<id>/replay    → Incident replay timeline
    POST /api/incident/<id>/resolve   → Resolve an incident
    POST /api/incident/<id>/escalate  → Manual escalation
    GET  /api/telemetry/streams       → Active telemetry streams
    GET  /api/context/snapshot        → Current aggregated context
    GET  /api/context/location        → Current location info
    GET  /api/context/health          → Current health summary
    GET  /api/medical/profile         → Resident medical profile
    GET  /api/medical/packet/<id>     → Emergency medical packet
    POST /twilio/voice                → Twilio call webhook (initial)
    POST /twilio/respond              → Twilio speech gather webhook
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from flask import Flask, Response, jsonify, request

logger = logging.getLogger(__name__)


def create_app(engine: Any) -> Flask:
    """Create the Flask app with all routes.

    Args:
        engine: The OpenClaw engine instance providing access
                to all subsystems (incident_manager, context,
                telemetry, replay, medical_profile, etc.)
    """
    app = Flask("openclaw")
    app.config["JSON_SORT_KEYS"] = False

    # ── Health & Status ──────────────────────────────────

    @app.route("/api/status")
    def get_status() -> tuple:
        """Service health and statistics."""
        return jsonify({
            "service": "openclaw",
            "status": "running",
            "uptime_seconds": time.time() - engine.started_at,
            "mqtt_connected": engine.mqtt.is_connected,
            "incidents": engine.incidents.stats,
            "telemetry_streams": len(
                engine.telemetry.get_active_streams()
            ),
            "active_replays": len(
                engine.replay.get_active_replays()
            ),
        }), 200

    # ── Incidents ────────────────────────────────────────

    @app.route("/api/incidents/active")
    def get_active_incidents() -> tuple:
        """Get all active incidents."""
        active = engine.incidents.get_active_incidents()
        return jsonify({
            "count": len(active),
            "incidents": [i.to_summary() for i in active],
        }), 200

    @app.route("/api/incidents/recent")
    def get_recent_incidents() -> tuple:
        """Get recent incidents."""
        limit = request.args.get("limit", 20, type=int)
        recent = engine.incidents.get_recent(limit)
        return jsonify({
            "count": len(recent),
            "incidents": [i.to_summary() for i in recent],
        }), 200

    @app.route("/api/incident/<incident_id>")
    def get_incident(incident_id: str) -> tuple:
        """Get full incident details."""
        incident = engine.incidents.get_incident(incident_id)
        if not incident:
            return jsonify({"error": "Incident not found"}), 404
        return jsonify(incident.to_dict()), 200

    @app.route("/api/incident/<incident_id>/resolve", methods=["POST"])
    def resolve_incident(incident_id: str) -> tuple:
        """Manually resolve an incident."""
        body = request.get_json(silent=True) or {}
        resolution = body.get("resolution", "manual_resolution")
        incident = engine.incidents.resolve(incident_id, resolution)
        if not incident:
            return jsonify({"error": "Incident not found"}), 404

        engine.on_incident_resolved(incident)
        return jsonify({
            "status": "resolved",
            "incident": incident.to_summary(),
        }), 200

    @app.route("/api/incident/<incident_id>/escalate", methods=["POST"])
    def escalate_incident(incident_id: str) -> tuple:
        """Manually escalate an incident."""
        incident = engine.incidents.get_incident(incident_id)
        if not incident:
            return jsonify({"error": "Incident not found"}), 404
        if not incident.is_active:
            return jsonify({"error": "Incident is not active"}), 400

        engine.manual_escalate(incident_id)
        return jsonify({
            "status": "escalated",
            "incident": incident.to_summary(),
        }), 200

    # ── Replay ───────────────────────────────────────────

    @app.route("/api/incident/<incident_id>/replay")
    def get_replay(incident_id: str) -> tuple:
        """Get incident replay timeline."""
        replay = engine.replay.get_replay(incident_id)
        if not replay:
            return jsonify({"error": "Replay not found"}), 404
        return jsonify(replay.to_dict()), 200

    # ── Telemetry ────────────────────────────────────────

    @app.route("/api/telemetry/streams")
    def get_telemetry_streams() -> tuple:
        """Get active telemetry streams."""
        streams = engine.telemetry.get_active_streams()
        return jsonify({
            "count": len(streams),
            "streams": streams,
        }), 200

    # ── Context ──────────────────────────────────────────

    @app.route("/api/context/snapshot")
    def get_context_snapshot() -> tuple:
        """Get full aggregated context snapshot."""
        return jsonify(engine.context.get_snapshot()), 200

    @app.route("/api/context/location")
    def get_location() -> tuple:
        """Get current location info."""
        return jsonify(engine.context.get_location_info()), 200

    @app.route("/api/context/health")
    def get_health_summary() -> tuple:
        """Get current health data summary."""
        hr_trend = engine.context.get_heart_rate_trend()
        spo2 = engine.context.get_latest("health", "spo2")
        steps = engine.context.get_latest("health", "steps")
        stress = engine.context.get_latest("health", "stress")
        return jsonify({
            "heart_rate": hr_trend,
            "spo2": spo2,
            "steps": steps,
            "stress": stress,
        }), 200

    # ── Medical Profile ──────────────────────────────────

    @app.route("/api/medical/profile")
    def get_medical_profile() -> tuple:
        """Get resident medical profile."""
        return jsonify(engine.medical.to_dict()), 200

    @app.route("/api/medical/packet/<incident_id>")
    def get_medical_packet(incident_id: str) -> tuple:
        """Generate emergency medical packet for an incident."""
        incident = engine.incidents.get_incident(incident_id)
        if not incident:
            return jsonify({"error": "Incident not found"}), 404

        location = engine.context.get_location_info()
        hr = engine.context.get_latest("health", "heart_rate")
        spo2 = engine.context.get_latest("health", "spo2")

        packet = engine.medical.build_emergency_packet(
            incident_data=incident.to_dict(),
            vitals={"heart_rate": hr, "spo2": spo2},
            location=location,
        )
        return jsonify(packet), 200

    # ── Twilio Conversational Webhooks ───────────────────

    def _twiml(body: str) -> Response:
        """Return a TwiML XML response."""
        return Response(body, content_type="application/xml")

    def _build_incident_briefing(incident_id: str) -> str:
        """Build a spoken briefing for the emergency responder.

        Gathers all available context: person details, vitals,
        location, reasons for the alert.
        """
        incident = engine.incidents.get_incident(incident_id)
        profile = engine.medical
        ctx = engine.context.build_context()

        person = profile.name or "a resident"
        age = profile.age
        address = profile.address or "unknown address"
        room = (
            incident.room
            if incident
            else (ctx.room or "unknown")
        )

        hr = ctx.heart_rate
        spo2 = ctx.spo2
        vitals_parts: list[str] = []
        if hr is not None:
            vitals_parts.append(f"heart rate {hr} B P M")
        if spo2 is not None:
            vitals_parts.append(f"oxygen saturation {spo2} percent")
        vitals_text = (
            ", ".join(vitals_parts) if vitals_parts else "unavailable"
        )

        # Extract reasons
        reasons_text = "a safety concern"
        if incident:
            for entry in reversed(incident.timeline):
                if (
                    entry.event == "escalated"
                    and "reasons" in entry.details
                ):
                    reasons_text = ", ".join(
                        entry.details["reasons"][:3]
                    )
                    break

        level = (
            incident.level.name
            if incident and hasattr(incident.level, "name")
            else "CRITICAL"
        )

        return (
            f"Emergency alert from the Elderly Tracking "
            f"and Monitoring System. "
            f"A {level} level incident has been detected. "
            f"Patient name: {person}, age {age}. "
            f"Located at {room}, {address}. "
            f"Current vitals: {vitals_text}. "
            f"Reason for alert: {reasons_text}. "
            f"Please respond to any questions you have "
            f"about the patient."
        )

    def _answer_question(
        speech: str, incident_id: str
    ) -> str:
        """Generate a spoken answer to the responder's question.

        Matches common emergency responder questions against
        available patient data and returns a concise reply.
        """
        profile = engine.medical
        ctx = engine.context.build_context()
        incident = engine.incidents.get_incident(incident_id)

        low = speech.lower()

        # ── Name / identity
        if any(
            w in low
            for w in ["name", "who", "patient", "person", "resident"]
        ):
            return (
                f"The patient's name is {profile.name}, "
                f"age {profile.age}."
            )

        # ── Location / address
        if any(
            w in low
            for w in ["where", "address", "location", "room", "floor"]
        ):
            room = (
                incident.room
                if incident
                else (ctx.room or "unknown")
            )
            floor = ctx.floor or profile.floor or "unknown"
            return (
                f"The patient is in {room}, "
                f"floor {floor}, "
                f"at {profile.address or 'address not available'}."
            )

        # ── Vitals / heart rate / oxygen
        if any(
            w in low
            for w in [
                "vital", "heart", "pulse", "rate",
                "oxygen", "spo2", "saturation", "bp",
                "breathing",
            ]
        ):
            hr = ctx.heart_rate
            spo2 = ctx.spo2
            parts: list[str] = []
            if hr is not None:
                parts.append(f"heart rate is {hr} B P M")
            if spo2 is not None:
                parts.append(
                    f"oxygen saturation is {spo2} percent"
                )
            if not parts:
                return "Current vitals are not available."
            return f"Current vitals: {', '.join(parts)}."

        # ── Medical history / conditions
        if any(
            w in low
            for w in [
                "medical", "history", "condition", "diagnosis",
                "disease", "health",
            ]
        ):
            conditions = profile.medical_conditions
            if conditions:
                return (
                    f"Known conditions: {', '.join(conditions)}."
                )
            return "No known medical conditions on record."

        # ── Allergies
        if any(w in low for w in ["allerg", "reaction"]):
            allergies = profile.allergies
            if allergies:
                return f"Known allergies: {', '.join(allergies)}."
            return "No known allergies on record."

        # ── Medications
        if any(
            w in low for w in ["medication", "medicine", "drug", "prescription"]
        ):
            meds = profile.medications
            if meds:
                return f"Current medications: {', '.join(meds)}."
            return "No medications currently on record."

        # ── Blood type
        if any(w in low for w in ["blood", "type"]):
            bt = profile.blood_type
            if bt:
                return f"Blood type is {bt}."
            return "Blood type is not on record."

        # ── Emergency contacts
        if any(
            w in low
            for w in ["contact", "family", "relative", "next of kin"]
        ):
            contacts = profile.emergency_contacts or []
            if contacts:
                parts_c = [
                    f"{c.get('name', '?')} "
                    f"({c.get('relationship', '?')}, "
                    f"{c.get('phone', 'no number')})"
                    for c in contacts
                ]
                return (
                    f"Emergency contacts: {'; '.join(parts_c)}."
                )
            return "No emergency contacts on record."

        # ── What happened / reasons
        if any(
            w in low
            for w in [
                "what happened", "incident", "alert",
                "reason", "why", "cause", "trigger",
            ]
        ):
            if incident:
                for entry in reversed(incident.timeline):
                    if (
                        entry.event == "escalated"
                        and "reasons" in entry.details
                    ):
                        rlist = entry.details["reasons"]
                        return (
                            f"The alert was triggered because: "
                            f"{', '.join(rlist)}."
                        )
            return (
                "A safety concern was detected by the "
                "monitoring system."
            )

        # ── Age
        if "age" in low or "old" in low:
            return f"The patient is {profile.age} years old."

        # ── Consciousness / responsive
        if any(
            w in low
            for w in ["conscious", "responsive", "awake", "alert"]
        ):
            movement = ctx.movement_present
            voice = ctx.voice_response
            if movement or voice:
                return (
                    "Based on sensor data, the patient appears "
                    "to be responsive."
                )
            return (
                "The patient's responsiveness is uncertain "
                "based on current sensor data."
            )

        # ── Fallback
        return (
            f"I have information about the patient's identity, "
            f"age, location, vitals, medical history, allergies, "
            f"medications, blood type, and emergency contacts. "
            f"Could you please rephrase your question?"
        )

    @app.route("/twilio/voice", methods=["POST", "GET"])
    def twilio_voice_webhook() -> Response:
        """Handle initial Twilio call — deliver briefing and listen.

        Twilio calls this URL when the call connects. We speak
        the emergency briefing and then use <Gather> to listen
        for the responder's questions via speech recognition.
        """
        incident_id = request.args.get(
            "incident_id",
            request.form.get("incident_id", "unknown"),
        )
        logger.info(
            "Twilio voice webhook called for incident %s",
            incident_id,
        )

        briefing = _build_incident_briefing(incident_id)

        twiml_body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Gather input='speech' "
            f"action='/twilio/respond?incident_id={incident_id}' "
            "method='POST' "
            "speechTimeout='auto' "
            "language='en-US'>"
            f"<Say voice='Polly.Joanna'>{briefing} "
            "You can now ask me any questions about the patient."
            "</Say>"
            "</Gather>"
            "<Say voice='Polly.Joanna'>"
            "I did not hear a response. "
            "The emergency briefing has been delivered. "
            "Please contact the provided emergency contacts "
            "for more information. Goodbye."
            "</Say>"
            "</Response>"
        )

        return _twiml(twiml_body)

    @app.route("/twilio/respond", methods=["POST"])
    def twilio_respond_webhook() -> Response:
        """Handle responder speech input and answer questions.

        Twilio sends the transcribed speech here. We match it
        against patient data and reply, then listen again for
        more questions — enabling a multi-turn conversation.
        """
        incident_id = request.args.get(
            "incident_id",
            request.form.get("incident_id", "unknown"),
        )
        speech = request.form.get("SpeechResult", "")
        confidence = request.form.get("Confidence", "?")

        logger.info(
            "Twilio speech received for incident %s: "
            "'%s' (confidence: %s)",
            incident_id,
            speech,
            confidence,
        )

        if not speech.strip():
            answer = (
                "I did not catch that. "
                "Could you please repeat your question?"
            )
        else:
            # Check for goodbye / end-call phrases
            low = speech.lower().strip()
            if any(
                phrase in low
                for phrase in [
                    "goodbye", "bye", "thank you", "thanks",
                    "that's all", "no more", "hang up", "end call",
                    "nothing else",
                ]
            ):
                twiml_body = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    "<Response>"
                    "<Say voice='Polly.Joanna'>"
                    "Thank you. Emergency services have been "
                    "notified. Stay safe. Goodbye."
                    "</Say>"
                    "</Response>"
                )
                return _twiml(twiml_body)

            answer = _answer_question(speech, incident_id)

        # Respond and listen for more questions
        twiml_body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Gather input='speech' "
            f"action='/twilio/respond?incident_id={incident_id}' "
            "method='POST' "
            "speechTimeout='auto' "
            "language='en-US'>"
            f"<Say voice='Polly.Joanna'>{answer} "
            "Do you have any other questions?"
            "</Say>"
            "</Gather>"
            "<Say voice='Polly.Joanna'>"
            "No further questions detected. "
            "The emergency briefing has been delivered. Goodbye."
            "</Say>"
            "</Response>"
        )

        return _twiml(twiml_body)

    # ── Auth0 JWT-protected routes ───────────────────────
    try:
        from api_auth import register_auth_routes
        register_auth_routes(app)
        logger.info("Auth0 JWT-protected routes registered")
    except ImportError:
        logger.warning(
            "api_auth not available — Auth0-protected routes disabled"
        )
    except Exception as exc:
        logger.warning("Failed to register Auth0 routes: %s", exc)

    return app


class APIServer:
    """Manages the REST API server lifecycle."""

    def __init__(
        self, engine: Any, host: str = "0.0.0.0", port: int = 8200
    ) -> None:
        self._engine = engine
        self._host = host
        self._port = port
        self._app = create_app(engine)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the API server in a background thread."""
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="openclaw-api",
        )
        self._thread.start()
        logger.info(
            "REST API server started on %s:%d",
            self._host,
            self._port,
        )

    def _run(self) -> None:
        """Run the Flask server."""
        self._app.run(
            host=self._host,
            port=self._port,
            debug=False,
            use_reloader=False,
        )
