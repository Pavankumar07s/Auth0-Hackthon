"""Action handlers for OpenClaw.

Each handler is responsible for executing a single type of action
(e.g., notify a caregiver, trigger a siren, send a voice prompt).
All handlers conform to a common interface for uniform dispatch
from the incident escalation pipeline.

Action types dispatched by the policy engine:
    notify_caregiver     → Telegram via PicoClaw
    emergency_call       → Simulated in dev / real in prod
    sms_caregiver        → Future: Twilio or similar
    unlock_door          → HA service call
    activate_siren       → HA service call
    activate_lights      → HA service call
    voice_check          → Alexa TTS via HA
    push_notification    → HA / mobile_app
    send_medical_packet  → REST payload to emergency services
    start_telemetry      → Start live telemetry stream
    stop_telemetry       → Stop live telemetry stream
    log_incident         → Persist incident data
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

import requests

logger = logging.getLogger(__name__)


class ActionHandler(Protocol):
    """Protocol for all action handlers."""

    def execute(
        self, action: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute the action and return result metadata."""
        ...


class HomeAssistantHandler:
    """Execute actions via Home Assistant REST API.

    Handles: unlock_door, activate_siren, activate_lights,
    voice_check (Alexa TTS via announce), push_notification.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8123",
        token: str = "",
        timeout: int = 10,
        alexa_entity_id: str = "media_player.alexa_sala_s_echo_dot",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._alexa_entity_id = alexa_entity_id
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if token:
            logger.info("HA handler configured: %s (token set)", base_url)
        else:
            logger.warning("HA handler configured WITHOUT token — API calls will fail")

    def execute(
        self, action: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch action to Home Assistant."""
        handler_map = {
            "unlock_door": self._unlock_door,
            "activate_siren": self._activate_siren,
            "activate_lights": self._activate_lights,
            "voice_check": self._voice_check,
            "push_notification": self._push_notification,
        }

        handler = handler_map.get(action)
        if not handler:
            logger.warning("Unknown HA action: %s", action)
            return {"success": False, "error": f"unknown_action:{action}"}

        try:
            return handler(context)
        except requests.RequestException as ex:
            logger.error("HA API error for %s: %s", action, ex)
            return {"success": False, "error": str(ex)}

    def _call_service(
        self, domain: str, service: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Call a HA service."""
        url = f"{self._base_url}/api/services/{domain}/{service}"
        resp = requests.post(
            url,
            headers=self._headers,
            json=data,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return {"success": True, "status_code": resp.status_code}

    def _unlock_door(self, context: dict[str, Any]) -> dict[str, Any]:
        """Unlock the main door for emergency access."""
        entity_id = context.get(
            "entity_id", "lock.front_door"
        )
        result = self._call_service(
            "lock", "unlock", {"entity_id": entity_id}
        )
        logger.info("Door unlocked: %s", entity_id)
        return result

    def _activate_siren(self, context: dict[str, Any]) -> dict[str, Any]:
        """Activate siren/alarm."""
        entity_id = context.get(
            "entity_id", "switch.emergency_siren"
        )
        return self._call_service(
            "switch", "turn_on", {"entity_id": entity_id}
        )

    def _activate_lights(
        self, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Turn on all lights in the affected room."""
        room = context.get("room", "")
        entity_id = context.get(
            "entity_id",
            f"light.{room}_ceiling" if room else "light.all_lights",
        )
        return self._call_service(
            "light",
            "turn_on",
            {
                "entity_id": entity_id,
                "brightness": 255,
                "color_temp": 250,
            },
        )

    def _voice_check(self, context: dict[str, Any]) -> dict[str, Any]:
        """Send voice prompt via Alexa Media Player announce.

        Uses the notify.alexa_media service from the HACS
        Alexa Media Player integration, which speaks a TTS
        announcement on the Echo device.
        """
        entity_id = context.get(
            "entity_id", self._alexa_entity_id
        )
        incident_id = context.get("incident_id", "unknown")
        person_name = context.get("person_name", "")

        message = (
            f"Attention. I detected a possible safety concern. "
            f"{person_name + ', are' if person_name else 'Are'} you okay? "
            f"If you are okay, please say, Alexa, I am fine. "
            f"If you need help, please say, Alexa, I am not okay. "
            f"If you do not respond within 20 seconds, "
            f"I will call emergency services."
        )

        result = self._call_service(
            "notify",
            "alexa_media",
            {
                "message": message,
                "target": [entity_id],
                "data": {"type": "announce"},
            },
        )
        logger.info(
            "Voice check (announce) sent via %s for incident %s",
            entity_id,
            incident_id,
        )
        return result

    def announce_message(
        self,
        message: str,
        entity_id: str = "",
    ) -> dict[str, Any]:
        """Send a generic Alexa TTS announcement.

        Used for follow-up messages after voice classification
        (e.g. reassurance, pre-emergency warning, re-prompts).
        """
        target = entity_id or self._alexa_entity_id
        try:
            result = self._call_service(
                "notify",
                "alexa_media",
                {
                    "message": message,
                    "target": [target],
                    "data": {"type": "announce"},
                },
            )
            logger.info(
                "Alexa announcement sent via %s: %.60s…",
                target,
                message,
            )
            return result
        except requests.RequestException as ex:
            logger.error("Alexa announcement failed: %s", ex)
            return {"success": False, "error": str(ex)}

    def _push_notification(
        self, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Send push notification via HA mobile app."""
        title = context.get("title", "ETMS Alert")
        message = context.get("message", "Safety alert triggered")
        target = context.get(
            "target", "notify.mobile_app_caregiver"
        )

        url = f"{self._base_url}/api/services/notify/{target.split('.')[-1]}"
        resp = requests.post(
            url,
            headers=self._headers,
            json={
                "title": title,
                "message": message,
                "data": {
                    "priority": "high",
                    "ttl": 0,
                    "importance": "high",
                },
            },
            timeout=self._timeout,
        )
        return {"success": resp.ok, "status_code": resp.status_code}

    def get_entity_state(
        self, entity_id: str
    ) -> dict[str, Any] | None:
        """Get current state of an HA entity via REST API.

        Returns the full state object including attributes,
        or None on failure.
        """
        url = f"{self._base_url}/api/states/{entity_id}"
        try:
            resp = requests.get(
                url,
                headers=self._headers,
                timeout=self._timeout,
            )
            if resp.ok:
                return resp.json()
        except requests.RequestException:
            pass
        return None

    def force_update_last_called(self) -> None:
        """Force-refresh Alexa last_called data from Amazon.

        Calls the ``alexa_media.update_last_called`` service
        so that ``last_called_summary`` and
        ``last_called_timestamp`` reflect the latest voice
        interaction immediately instead of waiting for the
        integration's next poll cycle (default 60 s).
        """
        try:
            self._call_service(
                "alexa_media", "update_last_called", {}
            )
        except Exception:
            logger.debug(
                "force_update_last_called failed — "
                "will fall back to cached state"
            )

    @property
    def alexa_entity_id(self) -> str:
        """Return the configured Alexa entity ID."""
        return self._alexa_entity_id


class TelegramHandler:
    """Send messages to caregivers directly via Telegram Bot API.

    Uses the Telegram Bot HTTP API (api.telegram.org) to send
    messages directly, bypassing PicoClaw for reliability.
    Also publishes to MQTT for dashboard/logging consumption.
    """

    TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
    TELEGRAM_PHOTO_API = "https://api.telegram.org/bot{token}/sendPhoto"
    TELEGRAM_VIDEO_API = "https://api.telegram.org/bot{token}/sendVideo"

    def __init__(
        self,
        bot_token: str = "",
        chat_ids: list[str] | None = None,
        mqtt_publish_fn: Any | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._default_chat_ids = chat_ids or []
        self._mqtt_publish = mqtt_publish_fn
        logger.info(
            "TelegramHandler configured: token=%s, chat_ids=%s",
            "set" if bot_token else "MISSING",
            self._default_chat_ids or "NONE",
        )

    def execute(
        self, action: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Send notification to caregivers via Telegram Bot API."""
        message = self._format_message(action, context)
        chat_ids = context.get("chat_ids", self._default_chat_ids)
        results = []

        # Send directly via Telegram Bot API
        if self._bot_token and chat_ids:
            url = self.TELEGRAM_API.format(token=self._bot_token)
            for chat_id in chat_ids:
                try:
                    resp = requests.post(
                        url,
                        json={
                            "chat_id": chat_id,
                            "text": message,
                        },
                        timeout=10,
                    )
                    if resp.ok:
                        results.append(
                            f"telegram_sent:{chat_id}"
                        )
                        logger.info(
                            "Telegram message sent to chat %s",
                            chat_id,
                        )
                    else:
                        logger.error(
                            "Telegram API error for chat %s: %s",
                            chat_id,
                            resp.text,
                        )
                except requests.RequestException as ex:
                    logger.error(
                        "Telegram send failed for chat %s: %s",
                        chat_id,
                        ex,
                    )
        elif not self._bot_token:
            logger.warning(
                "Telegram bot token not configured"
            )
        elif not chat_ids:
            logger.warning(
                "No Telegram chat IDs configured — message not sent"
            )

        # Also publish to MQTT for dashboard/logging
        if self._mqtt_publish:
            self._mqtt_publish(
                "etms/openclaw/telegram_sent",
                {
                    "action": action,
                    "message": message,
                    "chat_ids": chat_ids,
                    "results": results,
                },
            )

        return {"success": bool(results), "methods": results}

    def send_photo(
        self,
        photo_path: str,
        caption: str = "",
        chat_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send a photo to caregivers via Telegram Bot API."""
        targets = chat_ids or self._default_chat_ids
        if not self._bot_token or not targets:
            logger.warning("Cannot send photo: token=%s, chats=%d",
                           bool(self._bot_token), len(targets))
            return {"success": False, "error": "not_configured"}

        import os
        if not os.path.exists(photo_path):
            logger.warning("Snapshot file not found: %s", photo_path)
            return {"success": False, "error": "file_not_found"}

        url = self.TELEGRAM_PHOTO_API.format(token=self._bot_token)
        results = []
        for chat_id in targets:
            try:
                with open(photo_path, "rb") as f:
                    resp = requests.post(
                        url,
                        data={"chat_id": chat_id, "caption": caption},
                        files={"photo": f},
                        timeout=30,
                    )
                if resp.ok:
                    results.append(f"photo_sent:{chat_id}")
                    logger.info("Photo sent to chat %s", chat_id)
                else:
                    logger.error(
                        "Photo send failed for %s: %s", chat_id, resp.text
                    )
            except Exception as ex:
                logger.error("Photo send error for %s: %s", chat_id, ex)
        return {"success": bool(results), "results": results}

    def send_video(
        self,
        video_path: str,
        caption: str = "",
        chat_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send a video clip to caregivers via Telegram Bot API."""
        targets = chat_ids or self._default_chat_ids
        if not self._bot_token or not targets:
            return {"success": False, "error": "not_configured"}

        import os
        if not os.path.exists(video_path):
            logger.warning("Video file not found: %s", video_path)
            return {"success": False, "error": "file_not_found"}

        url = self.TELEGRAM_VIDEO_API.format(token=self._bot_token)
        results = []
        for chat_id in targets:
            try:
                with open(video_path, "rb") as f:
                    resp = requests.post(
                        url,
                        data={"chat_id": chat_id, "caption": caption},
                        files={"video": f},
                        timeout=60,
                    )
                if resp.ok:
                    results.append(f"video_sent:{chat_id}")
                    logger.info("Video clip sent to chat %s", chat_id)
                else:
                    logger.error(
                        "Video send failed for %s: %s", chat_id, resp.text
                    )
            except Exception as ex:
                logger.error("Video send error for %s: %s", chat_id, ex)
        return {"success": bool(results), "results": results}

    def _format_message(
        self, action: str, context: dict[str, Any]
    ) -> str:
        """Format a human-readable alert message."""
        # Allow raw message override (for status updates)
        if raw := context.get("_raw_message"):
            return raw

        level = context.get("level_name", "UNKNOWN")
        room = context.get("room", "unknown")
        person = context.get("person_name", "Resident")
        reasons = context.get("reasons", [])
        incident_id = context.get("incident_id", "")

        severity_emoji = {
            "CRITICAL": "🚨",
            "HIGH_RISK": "⚠️",
            "WARNING": "⚡",
            "MONITOR": "ℹ️",
        }
        emoji = severity_emoji.get(level, "📢")

        lines = [
            f"{emoji} ETMS {level} ALERT",
            f"Person: {person}",
            f"Location: {room}",
            "",
        ]
        if reasons:
            lines.append("Reasons:")
            for r in reasons:
                lines.append(f"  - {r}")
            lines.append("")

        hr = context.get("heart_rate")
        spo2 = context.get("spo2")
        if hr is not None:
            lines.append(f"Heart rate: {hr} bpm")
        if spo2 is not None:
            lines.append(f"SpO2: {spo2}%")

        if incident_id:
            lines.append(f"\nIncident: {incident_id}")

        return "\n".join(lines)


class EmergencyHandler:
    """Handle emergency service calls and medical packet dispatch.

    In development mode, this logs the action without making
    real calls. In production mode, it uses Twilio to place
    actual phone calls to emergency contacts.

    When ``public_url`` is set (e.g. an ngrok tunnel), the call
    uses Twilio's ``url`` parameter so the responder gets an
    interactive, conversational AI experience via speech
    recognition. Without it, the call falls back to inline
    TwiML with a static spoken message.
    """

    def __init__(
        self,
        mode: str = "development",
        twilio_account_sid: str = "",
        twilio_auth_token: str = "",
        twilio_from_number: str = "",
        emergency_to_number: str = "",
        twiml_message: str = "",
        public_url: str = "",
    ) -> None:
        self._mode = mode
        self._twilio_sid = twilio_account_sid
        self._twilio_token = twilio_auth_token
        self._from_number = twilio_from_number
        self._to_number = emergency_to_number
        self._public_url = public_url.rstrip("/") if public_url else ""
        self._twiml_message = twiml_message or (
            "This is an emergency alert from the Elderly Tracking "
            "and Monitoring System. A critical safety incident has "
            "been detected. Please check on the resident immediately "
            "and call emergency services if needed."
        )
        self._twilio_client: Any | None = None

        if (
            mode == "production"
            and twilio_account_sid
            and twilio_auth_token
        ):
            try:
                from twilio.rest import Client

                self._twilio_client = Client(
                    twilio_account_sid, twilio_auth_token
                )
                logger.info("Twilio client initialized for emergency calls")
            except ImportError:
                logger.error(
                    "twilio package not installed — "
                    "run: pip install twilio"
                )
            except Exception as ex:
                logger.error("Failed to init Twilio client: %s", ex)

    def execute(
        self, action: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute emergency action."""
        if action == "emergency_call":
            return self._emergency_call(context)
        if action == "send_medical_packet":
            return self._send_medical_packet(context)
        return {"success": False, "error": f"unknown:{action}"}

    def _emergency_call(
        self, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Initiate emergency call."""
        incident_id = context.get("incident_id", "unknown")
        level = context.get("level_name", "UNKNOWN")
        reasons = context.get("reasons", [])
        person = context.get("person_name", "Resident")
        room = context.get("room", "unknown")

        if self._mode == "development":
            logger.warning(
                "DEV MODE: Emergency call would be placed for "
                "incident %s, level=%s, reasons=%s",
                incident_id,
                level,
                reasons,
            )
            return {
                "success": True,
                "mode": "development",
                "simulated": True,
            }

        # Production mode — Twilio call
        if not self._twilio_client:
            logger.error(
                "Twilio client not available for emergency call"
            )
            return {
                "success": False,
                "error": "twilio_not_configured",
            }

        to_number = context.get("to_number", self._to_number)
        if not to_number:
            logger.error("No emergency phone number configured")
            return {
                "success": False,
                "error": "no_phone_number",
            }

        try:
            if self._public_url:
                # Conversational mode — Twilio fetches TwiML
                # from our webhook, enabling speech recognition
                # and multi-turn Q&A with the responder
                webhook_url = (
                    f"{self._public_url}/twilio/voice"
                    f"?incident_id={incident_id}"
                )
                call = self._twilio_client.calls.create(
                    url=webhook_url,
                    to=to_number,
                    from_=self._from_number,
                )
                logger.critical(
                    "CONVERSATIONAL EMERGENCY CALL placed for "
                    "incident %s — call SID: %s, to: %s, "
                    "webhook: %s",
                    incident_id,
                    call.sid,
                    to_number,
                    webhook_url,
                )
            else:
                # Static TwiML fallback — no webhook needed
                reason_text = (
                    ", ".join(reasons[:3])
                    if reasons
                    else "safety concern"
                )
                twiml = (
                    "<Response>"
                    "<Say voice='Polly.Joanna'>"
                    f"Emergency alert from E T M S. "
                    f"A {level} incident has been detected. "
                    f"Person: {person}. "
                    f"Location: {room}. "
                    f"Reason: {reason_text}. "
                    f"{self._twiml_message}"
                    "</Say>"
                    "<Pause length='2'/>"
                    "<Say voice='Polly.Joanna'>"
                    "Repeating the alert. "
                    f"A {level} incident. Person: {person}. "
                    f"Location: {room}. "
                    f"Please check on them immediately."
                    "</Say>"
                    "</Response>"
                )
                call = self._twilio_client.calls.create(
                    twiml=twiml,
                    to=to_number,
                    from_=self._from_number,
                )
                logger.critical(
                    "EMERGENCY CALL placed for incident %s — "
                    "call SID: %s, to: %s",
                    incident_id,
                    call.sid,
                    to_number,
                )
            return {
                "success": True,
                "mode": "production",
                "conversational": bool(self._public_url),
                "call_sid": call.sid,
                "to": to_number,
            }
        except Exception as ex:
            logger.error(
                "Twilio call failed for incident %s: %s",
                incident_id,
                ex,
            )
            return {
                "success": False,
                "mode": "production",
                "error": str(ex),
            }

    def _send_medical_packet(
        self, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Send medical data packet to emergency services."""
        packet = {
            "timestamp": time.time(),
            "incident_id": context.get("incident_id"),
            "patient": {
                "name": context.get("person_name"),
                "age": context.get("age"),
                "medical_conditions": context.get(
                    "medical_conditions", []
                ),
                "medications": context.get("medications", []),
                "allergies": context.get("allergies", []),
                "blood_type": context.get("blood_type"),
                "emergency_contacts": context.get(
                    "emergency_contacts", []
                ),
            },
            "vitals": {
                "heart_rate": context.get("heart_rate"),
                "spo2": context.get("spo2"),
            },
            "location": {
                "room": context.get("room"),
                "floor": context.get("floor"),
                "address": context.get("address"),
            },
            "incident_summary": context.get("reasons", []),
        }

        if self._mode == "development":
            logger.info(
                "DEV MODE: Medical packet would be sent: %s",
                json.dumps(packet, indent=2),
            )
            return {
                "success": True,
                "mode": "development",
                "packet": packet,
            }

        return {"success": True, "mode": "production", "packet": packet}


class ActionDispatcher:
    """Routes actions to the appropriate handler.

    Centralized dispatch point called by the OpenClaw engine
    after the policy engine produces a decision.
    """

    def __init__(
        self,
        ha_handler: HomeAssistantHandler | None = None,
        telegram_handler: TelegramHandler | None = None,
        emergency_handler: EmergencyHandler | None = None,
    ) -> None:
        self._ha = ha_handler or HomeAssistantHandler()
        self._telegram = telegram_handler or TelegramHandler()
        self._emergency = emergency_handler or EmergencyHandler()

        self._action_routing: dict[str, ActionHandler] = {
            "unlock_door": self._ha,
            "activate_siren": self._ha,
            "activate_lights": self._ha,
            "voice_check": self._ha,
            "push_notification": self._ha,
            "notify_caregiver": self._telegram,
            "sms_caregiver": self._telegram,
            "emergency_call": self._emergency,
            "send_medical_packet": self._emergency,
        }

    def dispatch(
        self, action: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a single action to its handler."""
        handler = self._action_routing.get(action)
        if not handler:
            logger.warning("No handler for action: %s", action)
            return {"success": False, "error": f"no_handler:{action}"}

        logger.info(
            "Dispatching action: %s (incident=%s, level=%s)",
            action,
            context.get("incident_id", "?"),
            context.get("level_name", "?"),
        )

        result = handler.execute(action, context)
        result["action"] = action
        result["dispatched_at"] = time.time()
        return result

    def dispatch_all(
        self, actions: list[str], context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Dispatch multiple actions and collect results."""
        results = []
        for action in actions:
            result = self.dispatch(action, context)
            results.append(result)
        return results
