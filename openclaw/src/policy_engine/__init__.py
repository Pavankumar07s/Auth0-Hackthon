"""Deterministic escalation policy engine.

This is the CORE safety logic of OpenClaw.  Every decision here is
deterministic — no LLM can override these rules.

Escalation Levels:
    MONITOR   (0) — Log only, single anomaly, no distress
    WARNING   (1) — Push notification, start monitoring timer
    HIGH_RISK (2) — SMS caregiver, auto-call, unlock door, lights on
    CRITICAL  (3) — Emergency call, unlock exits, siren, live telemetry
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class EscalationLevel(IntEnum):
    """Escalation severity levels — ordered for comparison."""

    MONITOR = 0
    WARNING = 1
    HIGH_RISK = 2
    CRITICAL = 3


@dataclass
class PolicyThresholds:
    """Configurable thresholds for the escalation policy."""

    hr_critical_low: int = 40
    hr_critical_high: int = 170
    hr_elevated: int = 140
    hr_baseline_pct_high: float = 0.40
    spo2_critical: int = 88
    spo2_warning: int = 92
    inactivity_high_risk_seconds: int = 120
    inactivity_warning_seconds: int = 300
    anomaly_score_warning: float = 0.3
    anomaly_score_high: float = 0.6

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyThresholds:
        """Create from config dict, using defaults for missing keys."""
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


@dataclass
class EscalationContext:
    """Aggregated context fed into the policy engine for evaluation."""

    # Fire / gas — environmental sensors
    fire_detected: bool = False
    gas_leak_detected: bool = False

    # Fall detection
    fall_detected: bool = False

    # Vital signs
    heart_rate: float | None = None
    spo2: float | None = None
    heart_rate_baseline: float = 72.0

    # Activity
    inactivity_seconds: float = 0.0
    movement_present: bool = True

    # Anomaly scores
    anomaly_score: float = 0.0
    behavior_anomaly: bool = False
    wandering_detected: bool = False

    # Vision-agent reasoning
    vision_agent_severity: str = "info"
    vision_agent_event_type: str = ""

    # Additional context
    room: str = ""
    floor: int = 1
    person_id: str = ""
    previous_level: EscalationLevel = EscalationLevel.MONITOR

    # Voice confirmation state
    voice_confirmation_pending: bool = False
    voice_response: str | None = None


@dataclass
class PolicyDecision:
    """Output of the policy engine evaluation."""

    level: EscalationLevel
    reasons: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    requires_voice_confirmation: bool = False
    skip_voice_confirmation: bool = False
    context: EscalationContext | None = None

    @property
    def level_name(self) -> str:
        """Human-readable level name."""
        return self.level.name

    def to_dict(self) -> dict[str, Any]:
        """Serialize for MQTT / REST."""
        return {
            "level": self.level.value,
            "level_name": self.level_name,
            "reasons": self.reasons,
            "actions": self.actions,
            "requires_voice_confirmation": self.requires_voice_confirmation,
            "skip_voice_confirmation": self.skip_voice_confirmation,
        }


class PolicyEngine:
    """Deterministic escalation policy engine.

    The decision tree is fully deterministic.  No LLM output can
    override these rules.  The tree is evaluated top-down — the first
    CRITICAL match short-circuits.
    """

    def __init__(self, thresholds: PolicyThresholds | None = None) -> None:
        self.thresholds = thresholds or PolicyThresholds()

    def evaluate(self, ctx: EscalationContext) -> PolicyDecision:
        """Run the deterministic decision tree against the context.

        Returns the highest applicable escalation level with all
        contributing reasons and required actions.
        """
        t = self.thresholds
        level = EscalationLevel.MONITOR
        reasons: list[str] = []
        actions: list[str] = []

        # ── CRITICAL checks (immediate emergency) ───────────────

        # Fire detected → CRITICAL (no confirmation needed)
        if ctx.fire_detected:
            level = EscalationLevel.CRITICAL
            reasons.append("Fire detected")
            actions.extend([
                "emergency_call",
                "unlock_exits",
                "activate_siren",
                "notify_caregiver",
                "start_telemetry",
            ])

        # Gas leak → CRITICAL
        if ctx.gas_leak_detected:
            level = EscalationLevel.CRITICAL
            reasons.append("Gas leak detected")
            actions.extend([
                "emergency_call",
                "unlock_exits",
                "activate_siren",
                "notify_caregiver",
                "start_telemetry",
            ])

        # Critical heart rate
        if ctx.heart_rate is not None:
            if ctx.heart_rate < t.hr_critical_low:
                level = EscalationLevel.CRITICAL
                reasons.append(
                    f"Heart rate critically low: {ctx.heart_rate} bpm "
                    f"(threshold: <{t.hr_critical_low})"
                )
                actions.extend([
                    "emergency_call",
                    "send_medical_packet",
                    "notify_caregiver",
                    "start_telemetry",
                ])
            elif ctx.heart_rate > t.hr_critical_high:
                level = EscalationLevel.CRITICAL
                reasons.append(
                    f"Heart rate critically high: {ctx.heart_rate} bpm "
                    f"(threshold: >{t.hr_critical_high})"
                )
                actions.extend([
                    "emergency_call",
                    "send_medical_packet",
                    "notify_caregiver",
                    "start_telemetry",
                ])

        # Critical SpO2
        if ctx.spo2 is not None and ctx.spo2 < t.spo2_critical:
            level = EscalationLevel.CRITICAL
            reasons.append(
                f"SpO2 critically low: {ctx.spo2}% "
                f"(threshold: <{t.spo2_critical}%)"
            )
            actions.extend([
                "emergency_call",
                "send_medical_packet",
                "notify_caregiver",
                "start_telemetry",
            ])

        # Fall + abnormal vitals → CRITICAL
        if ctx.fall_detected and self._has_abnormal_vitals(ctx):
            level = EscalationLevel.CRITICAL
            reasons.append("Fall detected with abnormal vital signs")
            actions.extend([
                "emergency_call",
                "send_medical_packet",
                "unlock_door",
                "notify_caregiver",
                "start_telemetry",
            ])

        # If already CRITICAL, skip lower checks
        if level == EscalationLevel.CRITICAL:
            actions = list(dict.fromkeys(actions))  # deduplicate
            return PolicyDecision(
                level=level,
                reasons=reasons,
                actions=actions,
                skip_voice_confirmation=True,
                context=ctx,
            )

        # ── HIGH_RISK checks ────────────────────────────────────

        # Fall + no movement
        if ctx.fall_detected and (
            ctx.inactivity_seconds > t.inactivity_high_risk_seconds
            or not ctx.movement_present
        ):
            level = max(level, EscalationLevel.HIGH_RISK)
            reasons.append(
                f"Fall detected with no movement for "
                f"{ctx.inactivity_seconds:.0f}s"
            )
            actions.extend([
                "sms_caregiver",
                "auto_call_caregiver",
                "unlock_door",
                "activate_lights",
                "voice_check",
            ])

        # SpO2 warning range
        if ctx.spo2 is not None and ctx.spo2 < t.spo2_warning:
            level = max(level, EscalationLevel.HIGH_RISK)
            reasons.append(
                f"SpO2 below warning threshold: {ctx.spo2}% "
                f"(threshold: <{t.spo2_warning}%)"
            )
            actions.extend([
                "sms_caregiver",
                "notify_caregiver",
                "voice_check",
            ])

        # Sustained elevated HR
        if (
            ctx.heart_rate is not None
            and ctx.heart_rate > t.hr_elevated
        ):
            level = max(level, EscalationLevel.HIGH_RISK)
            reasons.append(
                f"Heart rate sustained elevated: {ctx.heart_rate} bpm"
            )
            actions.extend([
                "sms_caregiver",
                "notify_caregiver",
                "voice_check",
            ])

        # HR significantly above baseline
        if (
            ctx.heart_rate is not None
            and ctx.heart_rate_baseline > 0
            and ctx.heart_rate
            > ctx.heart_rate_baseline * (1 + t.hr_baseline_pct_high)
        ):
            level = max(level, EscalationLevel.HIGH_RISK)
            reasons.append(
                f"Heart rate {ctx.heart_rate} bpm exceeds baseline "
                f"({ctx.heart_rate_baseline} bpm) by "
                f">{t.hr_baseline_pct_high * 100:.0f}%"
            )
            actions.extend(["notify_caregiver", "voice_check"])

        # Prolonged inactivity
        if ctx.inactivity_seconds > t.inactivity_high_risk_seconds and not ctx.movement_present:
            level = max(level, EscalationLevel.HIGH_RISK)
            reasons.append(
                f"No movement detected for "
                f"{ctx.inactivity_seconds:.0f}s"
            )
            actions.extend([
                "notify_caregiver",
                "voice_check",
                "activate_lights",
            ])

        # Behavior anomaly + wandering
        if ctx.behavior_anomaly and ctx.wandering_detected:
            level = max(level, EscalationLevel.HIGH_RISK)
            reasons.append("Behavior anomaly combined with wandering")
            actions.extend([
                "notify_caregiver",
                "voice_check",
            ])

        if level >= EscalationLevel.HIGH_RISK:
            actions = list(dict.fromkeys(actions))
            return PolicyDecision(
                level=level,
                reasons=reasons,
                actions=actions,
                requires_voice_confirmation=True,
                context=ctx,
            )

        # ── WARNING checks ──────────────────────────────────────

        # Fall without vitals concern
        if ctx.fall_detected:
            level = max(level, EscalationLevel.WARNING)
            reasons.append("Fall detected (vitals appear normal)")
            actions.extend([
                "push_notification",
                "voice_check",
                "start_monitoring_timer",
            ])

        # Wandering
        if ctx.wandering_detected:
            level = max(level, EscalationLevel.WARNING)
            reasons.append("Wandering behavior detected")
            actions.extend([
                "push_notification",
                "start_monitoring_timer",
            ])

        # Behavior anomaly
        if ctx.behavior_anomaly:
            level = max(level, EscalationLevel.WARNING)
            reasons.append("Behavioral anomaly detected by SmartGuard")
            actions.extend([
                "push_notification",
                "start_monitoring_timer",
            ])

        # Anomaly score above warning threshold
        if ctx.anomaly_score > t.anomaly_score_warning:
            level = max(level, EscalationLevel.WARNING)
            reasons.append(
                f"Anomaly score elevated: {ctx.anomaly_score:.3f}"
            )
            actions.extend([
                "push_notification",
                "start_monitoring_timer",
            ])

        # Prolonged inactivity (warning level)
        if ctx.inactivity_seconds > t.inactivity_warning_seconds:
            level = max(level, EscalationLevel.WARNING)
            reasons.append(
                f"Inactivity for {ctx.inactivity_seconds:.0f}s"
            )
            actions.extend(["push_notification"])

        if level >= EscalationLevel.WARNING:
            actions = list(dict.fromkeys(actions))
            return PolicyDecision(
                level=level,
                reasons=reasons,
                actions=actions,
                context=ctx,
            )

        # ── MONITOR ─────────────────────────────────────────────
        return PolicyDecision(
            level=EscalationLevel.MONITOR,
            reasons=["No actionable conditions detected"],
            actions=["log_only"],
            context=ctx,
        )

    def _has_abnormal_vitals(self, ctx: EscalationContext) -> bool:
        """Check if vitals are outside normal range."""
        t = self.thresholds
        if ctx.heart_rate is not None:
            if (
                ctx.heart_rate < t.hr_critical_low
                or ctx.heart_rate > t.hr_elevated
            ):
                return True
        if ctx.spo2 is not None and ctx.spo2 < t.spo2_warning:
            return True
        return False

    def handle_voice_response(
        self, decision: PolicyDecision, response: str | None
    ) -> PolicyDecision:
        """Process voice confirmation response and potentially escalate.

        Args:
            decision: The current policy decision (HIGH_RISK with voice check)
            response: The voice response ("yes", "help", "no", or None for timeout)

        Returns:
            Updated policy decision after voice confirmation processing.
        """
        if decision.level != EscalationLevel.HIGH_RISK:
            return decision

        if response is None:
            # No response within timeout → escalate to CRITICAL
            logger.warning(
                "No voice response received — escalating to CRITICAL"
            )
            return PolicyDecision(
                level=EscalationLevel.CRITICAL,
                reasons=decision.reasons + [
                    "No voice response after confirmation prompt"
                ],
                actions=[
                    "emergency_call",
                    "send_medical_packet",
                    "unlock_door",
                    "notify_caregiver",
                    "start_telemetry",
                    "activate_siren",
                ],
                skip_voice_confirmation=True,
                context=decision.context,
            )

        response_lower = response.strip().lower()

        # Positive responses — person is okay
        positive = {"yes", "yeah", "i'm fine", "i am fine", "okay", "ok", "im fine", "im ok", "i'm ok"}
        if any(p in response_lower for p in positive):
            logger.info("Positive voice response — downgrading to MONITOR")
            return PolicyDecision(
                level=EscalationLevel.MONITOR,
                reasons=decision.reasons + [
                    f"Resident confirmed they are okay (response: '{response}')"
                ],
                actions=["log_only", "notify_caregiver_resolved"],
                context=decision.context,
            )

        # Distress responses
        distress = {"help", "no", "can't", "hurt", "pain", "call", "emergency"}
        if any(d in response_lower for d in distress):
            logger.warning(
                "Distress voice response — escalating to CRITICAL"
            )
            return PolicyDecision(
                level=EscalationLevel.CRITICAL,
                reasons=decision.reasons + [
                    f"Resident expressed distress (response: '{response}')"
                ],
                actions=[
                    "emergency_call",
                    "send_medical_packet",
                    "unlock_door",
                    "notify_caregiver",
                    "start_telemetry",
                ],
                skip_voice_confirmation=True,
                context=decision.context,
            )

        # Unclear response — keep at HIGH_RISK, retry once
        logger.info("Unclear voice response: '%s' — maintaining HIGH_RISK", response)
        return PolicyDecision(
            level=EscalationLevel.HIGH_RISK,
            reasons=decision.reasons + [
                f"Unclear voice response: '{response}'"
            ],
            actions=decision.actions,
            requires_voice_confirmation=True,
            context=decision.context,
        )
