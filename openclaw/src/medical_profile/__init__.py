"""Medical profile module for OpenClaw.

Manages the digital medical profile of the monitored resident.
This data is critical for emergency medical packets sent to
first responders and for contextualizing policy decisions.

Features:
    - Structured medical history
    - Emergency contact management
    - Medical data packet generation for ambulance dispatch
    - Baseline vital signs for anomaly context
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EmergencyContact:
    """An emergency contact entry."""

    name: str
    phone: str
    relationship: str
    telegram_chat_id: str = ""
    is_primary: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "name": self.name,
            "phone": self.phone,
            "relationship": self.relationship,
            "telegram_chat_id": self.telegram_chat_id,
            "is_primary": self.is_primary,
        }


@dataclass
class MedicalProfile:
    """Complete medical profile for a monitored resident."""

    resident_id: str
    name: str
    age: int
    blood_type: str = ""
    address: str = ""
    medical_conditions: list[str] = field(default_factory=list)
    medications: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    emergency_contacts: list[EmergencyContact] = field(
        default_factory=list
    )
    baseline_heart_rate: float = 72.0
    baseline_spo2: float = 97.0
    notes: str = ""
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MedicalProfile:
        """Create profile from config dictionary."""
        contacts = [
            EmergencyContact(
                name=c.get("name", ""),
                phone=c.get("phone", ""),
                relationship=c.get("relationship", ""),
                telegram_chat_id=str(c.get("telegram_chat_id", "")),
                is_primary=c.get("is_primary", False),
            )
            for c in data.get("emergency_contacts", [])
        ]

        # Support nested medical_history dict from settings.yaml
        # as well as flat top-level keys
        history = data.get("medical_history", {})
        baseline = data.get("baseline", {})

        return cls(
            resident_id=data.get("id", ""),
            name=data.get("name", ""),
            age=data.get("age", 0),
            blood_type=(
                data.get("blood_type")
                or history.get("blood_type", "")
            ),
            address=data.get("address", ""),
            medical_conditions=(
                data.get("medical_conditions")
                or history.get("conditions", [])
            ),
            medications=(
                data.get("medications")
                or history.get("medications", [])
            ),
            allergies=(
                data.get("allergies")
                or history.get("allergies", [])
            ),
            emergency_contacts=contacts,
            baseline_heart_rate=(
                data.get("baseline_heart_rate")
                or baseline.get("heart_rate", 72.0)
            ),
            baseline_spo2=(
                data.get("baseline_spo2")
                or baseline.get("spo2", 97.0)
            ),
            notes=(
                data.get("notes")
                or history.get("notes", "")
            ),
        )

    def get_primary_contact(self) -> EmergencyContact | None:
        """Get the primary emergency contact."""
        for c in self.emergency_contacts:
            if c.is_primary:
                return c
        return self.emergency_contacts[0] if self.emergency_contacts else None

    def get_telegram_chat_ids(self) -> list[str]:
        """Get all Telegram chat IDs for notification."""
        return [
            c.telegram_chat_id
            for c in self.emergency_contacts
            if c.telegram_chat_id
        ]

    def build_emergency_packet(
        self,
        incident_data: dict[str, Any] | None = None,
        vitals: dict[str, Any] | None = None,
        location: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a complete medical data packet for emergency dispatch.

        This packet contains everything a first responder needs:
        patient info, current vitals, medical history, and incident
        context.
        """
        packet = {
            "generated_at": time.time(),
            "patient": {
                "name": self.name,
                "age": self.age,
                "blood_type": self.blood_type,
                "address": self.address,
                "medical_conditions": self.medical_conditions,
                "medications": self.medications,
                "allergies": self.allergies,
                "baseline_vitals": {
                    "heart_rate": self.baseline_heart_rate,
                    "spo2": self.baseline_spo2,
                },
                "notes": self.notes,
            },
            "emergency_contacts": [
                c.to_dict() for c in self.emergency_contacts
            ],
        }

        if vitals:
            packet["current_vitals"] = vitals
        if location:
            packet["location"] = location
        if incident_data:
            packet["incident"] = incident_data

        return packet

    def build_context_for_actions(
        self,
        incident_id: str = "",
        room: str = "",
        floor: int = 1,
        heart_rate: float | None = None,
        spo2: float | None = None,
        level_name: str = "",
        reasons: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build enriched context dict for action handlers."""
        primary = self.get_primary_contact()
        return {
            "incident_id": incident_id,
            "person_name": self.name,
            "age": self.age,
            "room": room,
            "floor": floor,
            "heart_rate": heart_rate,
            "spo2": spo2,
            "level_name": level_name,
            "reasons": reasons or [],
            "blood_type": self.blood_type,
            "medical_conditions": self.medical_conditions,
            "medications": self.medications,
            "allergies": self.allergies,
            "address": self.address,
            "chat_ids": self.get_telegram_chat_ids(),
            "primary_contact_name": (
                primary.name if primary else ""
            ),
            "primary_contact_phone": (
                primary.phone if primary else ""
            ),
            "emergency_contacts": [
                c.to_dict() for c in self.emergency_contacts
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        """Full serialization."""
        return {
            "resident_id": self.resident_id,
            "name": self.name,
            "age": self.age,
            "blood_type": self.blood_type,
            "address": self.address,
            "medical_conditions": self.medical_conditions,
            "medications": self.medications,
            "allergies": self.allergies,
            "emergency_contacts": [
                c.to_dict() for c in self.emergency_contacts
            ],
            "baseline_heart_rate": self.baseline_heart_rate,
            "baseline_spo2": self.baseline_spo2,
            "notes": self.notes,
            "updated_at": self.updated_at,
        }
