# OpenClaw — Emergency Orchestration Engine

**OpenClaw** is a deterministic emergency orchestration engine designed for the
ETMS (Elderly Tracking & Monitoring System). It aggregates real-time data from
multiple safety services—Vision, SmartGuard, health sensors, and environmental
monitors—and applies a strict policy engine to decide when and how to escalate
emergencies.

> **Safety-first design**: Every escalation decision is deterministic.  No LLM
> output can override the policy engine.  LLMs are used only for summarization
> (daily reports).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       OpenClaw Engine                           │
│                                                                 │
│  ┌──────────────┐  ┌─────────────┐  ┌───────────────────────┐  │
│  │    Context    │  │   Policy    │  │   Incident Manager    │  │
│  │  Aggregator   │→│   Engine    │→│  (state machine)       │  │
│  └──────────────┘  └─────────────┘  └───────────────────────┘  │
│         ↑                                      ↓                │
│  ┌──────────────┐                  ┌───────────────────────┐   │
│  │  MQTT Bridge  │                  │  Action Dispatcher    │   │
│  │  (paho v2)   │                  │  ├─ HA Handler        │   │
│  └──────────────┘                  │  ├─ Telegram Handler  │   │
│         ↑                          │  └─ Emergency Handler │   │
│         │                          └───────────────────────┘   │
│  ┌──────────────┐  ┌─────────────┐  ┌───────────────────────┐  │
│  │   Medical     │  │  Telemetry  │  │   Replay Builder     │  │
│  │   Profile     │  │  Manager    │  │   (timeline)         │  │
│  └──────────────┘  └─────────────┘  └───────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                     REST API (Flask)                      │   │
│  │  /api/status  /api/incidents  /api/context  /api/medical  │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
         ↕ MQTT                    ↕ HTTP
┌─────────────────┐        ┌─────────────────┐
│  Mosquitto MQTT  │        │    PicoClaw      │
│  (localhost:1883)│        │  (Telegram bot)  │
└─────────────────┘        └─────────────────┘
         ↕                          ↕
┌─────────────────┐        ┌─────────────────┐
│ Home Assistant   │        │  Family on       │
│ (automations)    │        │  Telegram         │
└─────────────────┘        └─────────────────┘
```

---

## Core Modules

### 1. Policy Engine (`src/policy_engine/`)

The **deterministic decision tree** — the most critical safety component.

**Escalation levels:**

| Level | Value | Description | Actions |
|-------|-------|-------------|---------|
| `MONITOR` | 0 | Normal — log only | None |
| `WARNING` | 1 | Single anomaly — push notification | Notify, start timer |
| `HIGH_RISK` | 2 | Requires attention — voice check | SMS, auto-call, lights, voice |
| `CRITICAL` | 3 | Emergency — immediate response | 112 call, siren, unlock exits |

**Key rules (top-down evaluation):**

- Fire / gas leak → **CRITICAL** (no voice confirmation)
- Heart rate < 40 or > 170 → **CRITICAL**
- SpO2 < 88% → **CRITICAL**
- Fall + abnormal vitals → **CRITICAL**
- Fall + no movement → **HIGH_RISK** (voice confirmation required)
- SpO2 < 92% → **HIGH_RISK**
- Heart rate > 140 → **HIGH_RISK**
- Fall alone → **WARNING**
- Wandering → **WARNING**
- Anomaly score > 0.3 → **WARNING**

**Voice confirmation flow:**

When the policy requires voice confirmation (HIGH_RISK level):
1. OpenClaw publishes a voice request via MQTT
2. Home Assistant automation triggers Alexa TTS: "Are you okay?"
3. Response is routed back via MQTT within 30 seconds
4. Positive response ("I'm fine") → downgrade to MONITOR
5. Distress response ("help") → escalate to CRITICAL
6. No response / timeout → escalate to CRITICAL

### 2. Incident Manager (`src/incident_manager/`)

Manages the full lifecycle of safety incidents.

**State machine:**

```
DETECTED → ASSESSING → ESCALATED → VOICE_PENDING → RESOLVED
                                                   → EXPIRED
```

**Features:**
- **Deduplication**: Prevents duplicate incidents for the same event within a
  configurable window (default: 30 seconds)
- **Auto-expiration**: Unresolved incidents expire after a configurable timeout
- **Timeline tracking**: Every state change, escalation, and action is recorded
- **Statistics**: Total incidents, escalation count, active count

### 3. Context Aggregator (`src/context_aggregator/`)

Fuses data from all sensor sources into a unified `EscalationContext` snapshot.

**Data sources:**
- **Vision-Agent**: Fall detection, wandering, person tracking, room location
- **SmartGuard**: Behavior anomaly scores
- **Health sensors**: Heart rate, SpO2, steps, stress (Noise smartwatch via HA)
- **Environmental**: Fire detector, gas sensor, door sensors
- **Voice**: Alexa confirmation responses

**Features:**
- Rolling window (configurable, default 120 seconds)
- Max 200 readings per sensor key
- Thread-safe with lock
- Heart rate trend analysis (min/max/avg/count)
- Location tracking (room, floor, person ID)

### 4. MQTT Bridge (`src/mqtt_bridge/`)

Handles all MQTT communication using `paho-mqtt` v2 API.

**Subscribed topics:**
- `vision_agent/reasoned_event` — Vision-Agent AI decisions
- `smartguard/anomaly` — SmartGuard anomaly scores
- `homeassistant/sensor/+/state` — Health sensor data
- `etms/fire_alarm`, `etms/gas_sensor` — Environmental alerts
- `etms/voice/response` — Alexa voice confirmation responses

**Published topics:**
- `etms/openclaw/incident` — Incident updates
- `etms/openclaw/action` — Action dispatches
- `etms/openclaw/telemetry/<incident_id>` — Live vitals streaming
- `etms/openclaw/status` — Service heartbeat
- `etms/openclaw/daily_report` — Daily safety summary
- `etms/voice/request` — Voice confirmation requests

### 5. Action Handlers (`src/action_handlers/`)

Routes actions to the correct execution handler.

**Handlers:**
- **HomeAssistantHandler**: REST API calls to HA
  - `unlock_door`, `activate_siren`, `activate_lights`, `voice_check`, `push_notification`
- **TelegramHandler**: Alerts via PicoClaw + MQTT
  - Dual-path: MQTT publish to PicoClaw topic + REST API fallback
  - Rich Markdown messages with emoji, vitals, and action context
- **EmergencyHandler**: Emergency services
  - Development mode: simulates calls (no real 112 dialing)
  - Production mode: integrates with emergency APIs
  - Medical packet builder for ambulance dispatch

### 6. Medical Profile (`src/medical_profile/`)

Digital medical profile for the resident.

**Contains:**
- Personal info (name, age, blood type, address)
- Medical conditions, medications, allergies
- Emergency contacts with Telegram chat IDs
- Baseline vitals (heart rate, SpO2)

**Emergency packet**: Structured data package for ambulance dispatch containing
patient info, current vitals, location, incident details, and medical history.

### 7. Telemetry (`src/telemetry/`)

Live vitals streaming during active incidents.

- Background daemon threads with configurable interval (default: 5 seconds)
- Per-incident streams — start when escalated, stop on resolution
- Publishes to `etms/openclaw/telemetry/<incident_id>`
- Sample count tracking for statistics

### 8. Replay (`src/replay/`)

Incident timeline reconstruction for post-incident analysis.

- Records all events with timestamps during an incident
- Pre-context capture (events before incident detection)
- Post-context capture (events after resolution)
- Full timeline export with relative seconds

### 9. REST API (`src/rest_api/`)

Flask-based HTTP API for querying OpenClaw state. Used by PicoClaw's ETMS tools.

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Service status with uptime |
| GET | `/api/incidents/active` | All active incidents |
| GET | `/api/incidents/recent` | Recent incidents (last 24h) |
| GET | `/api/incident/<id>` | Single incident details |
| POST | `/api/incident/<id>/resolve` | Resolve an incident |
| POST | `/api/incident/<id>/escalate` | Manually escalate |
| GET | `/api/incident/<id>/replay` | Incident replay timeline |
| GET | `/api/telemetry/streams` | Active telemetry streams |
| GET | `/api/context/snapshot` | Full context snapshot |
| GET | `/api/context/location` | Current room/person location |
| GET | `/api/context/health` | Latest health data |
| GET | `/api/medical/profile` | Resident medical profile |
| GET | `/api/medical/packet/<id>` | Emergency packet for incident |

---

## PicoClaw Integration

[PicoClaw](https://github.com/kha-tul/picoclaw) is a Go-based AI agent that
connects to Telegram, Discord, and other chat platforms. For ETMS, it provides
a **conversational interface** for family caregivers to query the system.

### ETMS Tools (`pkg/tools/etms.go`)

Six Go tools query OpenClaw's REST API:

| Tool | Purpose | Example Query |
|------|---------|--------------|
| `etms_status` | Overall system status | "How is my mother?" |
| `etms_health` | Current vitals | "What's her heart rate?" |
| `etms_location` | Room location | "Where is she right now?" |
| `etms_incidents` | Active/recent alerts | "Were there any problems today?" |
| `etms_medical` | Medical profile | "What medications does she take?" |
| `etms_command` | Resolve/escalate | "Cancel that alert" |

### Configuration

PicoClaw config (`~/.picoclaw/config.yaml`):
```yaml
tools:
  etms:
    enabled: true
    openclaw_url: "http://localhost:8200"
```

### System Prompt

The ETMS caregiver assistant prompt is loaded from `~/.picoclaw/workspace/AGENTS.md`.
It instructs the LLM to be warm, simple, and proactive when responding to family
members asking about their elderly relative.

---

## Home Assistant Integration

### MQTT Sensors

OpenClaw publishes state to MQTT which HA tracks via sensors defined in
`configuration.yaml`:

- `binary_sensor.openclaw_online` — Service online/offline
- `sensor.openclaw_incident_level` — Current escalation level (0–3)
- `sensor.openclaw_incident_id` — Active incident ID
- `sensor.openclaw_incident_state` — Incident state machine position
- `sensor.openclaw_incident_trigger` — What triggered the incident
- `sensor.openclaw_incident_room` — Room where incident occurred
- `sensor.openclaw_last_action` — Last action dispatched
- `sensor.openclaw_telemetry_heart_rate` — Live HR during incidents

### Automations

Seven automations in `automations.yaml`:

1. **Voice check**: MQTT voice request → Alexa TTS "Are you okay?"
2. **Critical incident**: Level = CRITICAL → persistent notification
3. **High-risk alert**: Level = HIGH_RISK → notification with voice info
4. **Incident resolved**: State = RESOLVED → dismiss + notification
5. **Emergency lights**: `activate_lights` action → all lights to max
6. **Service offline**: OpenClaw offline > 30s → alert notification
7. **Daily report**: Scheduled report → formatted notification

### Dashboard

The `ui-lovelace.yaml` includes an **OpenClaw** tab with:
- Service status glance (online/offline, alert level, state, status)
- Escalation level gauge (0–3 with color severity)
- Active incident details card (ID, trigger, room, state, level, last action)
- Conditional live telemetry gauge (shown when level > 1)
- Incident timeline logbook (24-hour history)
- Conditional voice pending status message
- Conditional service offline warning with startup command
- Emergency action buttons (lights, siren silence, door unlock)

---

## Configuration

All configuration is in `config/settings.yaml`:

```yaml
mqtt:
  broker: localhost
  port: 1883
  username: mqtt_user
  password: Pavan@2005

policy:
  thresholds:
    hr_critical_low: 40
    hr_critical_high: 170
    spo2_critical: 88
    spo2_warning: 92

voice_confirmation:
  enabled: true
  timeout_seconds: 30

resident:
  name: "Resident Name"
  age: 75
  emergency_contacts:
    - name: "Son"
      phone: "+1234567890"
      telegram_chat_id: "12345"

service:
  rest_port: 8200
```

---

## Running

### Prerequisites

- Python 3.10+
- Mosquitto MQTT broker running on localhost:1883
- Home Assistant running with MQTT integration
- (Optional) PicoClaw for Telegram bot

### Install

```bash
cd ~/Desktop/Autism/openclaw
pip install -r requirements.txt
```

### Start

```bash
python run.py
# or with custom config:
python run.py config/settings.yaml
```

### Verify

```bash
# Check service status
curl http://localhost:8200/api/status

# Check context snapshot
curl http://localhost:8200/api/context/snapshot

# Check health data
curl http://localhost:8200/api/context/health
```

---

## Testing

85 tests covering all core modules:

```bash
python -m pytest tests/test_openclaw.py -v
```

**Test categories:**
- `TestPolicyEngine` — 15 tests: escalation levels, threshold combinations, voice response handling
- `TestIncidentManager` — 10 tests: lifecycle, dedup, escalation, resolution, stats
- `TestContextAggregator` — 15 tests: ingestion, context building, trends, snapshots
- `TestMedicalProfile` — 7 tests: loading, contacts, emergency packets
- `TestActionDispatcher` — 6 tests: routing, handler execution, batch dispatch
- `TestTelemetryManager` — 5 tests: stream lifecycle, concurrent streams
- `TestReplayBuilder` — 6 tests: recording, completion, pre-context
- `TestEndToEndPipeline` — 4 tests: full pipeline scenarios (fall, fire, voice, health)

---

## ETMS System Architecture

OpenClaw is the **central orchestration layer** in the ETMS platform:

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────┐
│  Camera 1    │  │  Camera 2    │  │   Noise      │  │  Fire /  │
│  (C270 HD)   │  │  (UVC)       │  │  Smartwatch  │  │  Gas     │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────┬─────┘
       │                 │                 │               │
       ▼                 ▼                 ▼               │
┌─────────────────────────────┐     ┌──────────────┐      │
│      Vision Service          │     │ Home Assistant│◄─────┘
│  (YOLO, multi-camera, Re-ID)│     │  (sensors)    │
└──────────────┬───────────────┘     └──────┬───────┘
               │                            │
               ▼                            ▼
┌──────────────────────┐          ┌─────────────────┐
│     Vision-Agent      │          │   SmartGuard     │
│  (AI reasoning +     │          │  (anomaly det.)  │
│   multimodal fusion) │          └────────┬─────────┘
└──────────┬────────────┘                   │
           │              ┌─────────────────┘
           ▼              ▼
┌──────────────────────────────┐
│         OpenClaw              │
│  (emergency orchestration)   │
│  ┌────────┐ ┌─────────────┐ │
│  │ Policy │ │  Incident   │ │
│  │ Engine │ │  Manager    │ │
│  └────────┘ └─────────────┘ │
└──────┬───────────┬───────────┘
       │           │
       ▼           ▼
┌──────────┐ ┌───────────┐ ┌──────────┐
│   Alexa   │ │  PicoClaw  │ │ Emergency │
│  (voice)  │ │ (Telegram) │ │ Services  │
└──────────┘ └───────────┘ └──────────┘
```

---

## Innovative Features

### 1. Voice Confirmation via Alexa
Before calling emergency services, OpenClaw asks the resident "Are you okay?"
through the Amazon Alexa speaker. This prevents false alarms while maintaining
rapid response for real emergencies.

### 2. Conversational Caregiver Queries
Family members can ask natural-language questions via Telegram through PicoClaw:
"How is my mother?", "Where is she?", "Were there any problems today?" — and
receive warm, informative responses powered by the LLM + ETMS tools.

### 3. Live Telemetry Streaming
During active incidents, OpenClaw streams real-time vitals (heart rate, SpO2)
every 5 seconds via MQTT, enabling caregivers and emergency responders to
monitor the situation remotely.

### 4. Digital Medical Profile & Emergency Packets
When emergency services are called, OpenClaw automatically sends a structured
medical packet containing patient info, current vitals, GPS coordinates, medical
history, and incident context — reducing response time.

### 5. Incident Replay
After resolution, the complete timeline of every incident can be reviewed:
sensor readings before detection, each escalation step, actions taken, and
resolution details. This supports post-incident analysis and system improvement.

### 6. Daily AI Safety Report
Every evening at 9 PM, OpenClaw generates a daily summary: total incidents,
health trends, location patterns, and any anomalies detected — delivered as a
push notification and via Telegram.

### 7. Multi-Path Notification
Every alert is delivered through multiple channels simultaneously: Home
Assistant notifications, Alexa announcements, Telegram messages (via PicoClaw),
and SMS for critical events — ensuring no alert is missed.
