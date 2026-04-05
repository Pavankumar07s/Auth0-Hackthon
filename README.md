# ETMS × Auth0 — Elderly Tracking & Monitoring System
### *"Authorized to Act: Auth0 for AI Agents"* — Auth0 AI Agents Hackathon 2026

> **Real-time multi-agent safety platform for elderly independence, powered by edge AI and Auth0's full AI Agents security toolkit.**

---

## Table of Contents
1. [Quick Start](#quick-start)
2. [Project Overview](#project-overview)
3. [Architecture](#architecture)
4. [Auth0 Features](#auth0-features)
5. [Prerequisites](#prerequisites)
6. [Environment Setup](#environment-setup)
7. [Auth0 Configuration](#auth0-configuration)
8. [Running All Services](#running-all-services)
9. [Service Reference](#service-reference)
10. [How It Works](#how-it-works)

---

## Quick Start

```bash
# Clone and enter project
cd "/home/pavan/Desktop/Auth0 hackthon/AuthoHackthon"

# 1. Install Auth0 Python dependencies
pip install -r requirements-auth0.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your Auth0 credentials

# 3. Link connected accounts (one-time)
python auth0/login_helper.py google   # Link Google Calendar
python auth0/login_helper.py slack    # Link Slack

# 4. Verify Auth0 integration (18 tests)
python auth0/test_full_flow.py

# 5. Start all services (see below)
```

---

## Running All Services

Open **8 terminals** and run each command:

### Terminal 1 — MQTT Broker (verify it's running)
```bash
# Monitor SmartGuard anomaly events
mosquitto_sub -h localhost -u mqtt_user -P 'Pavan@2005' -t 'etms/smartguard/#' -v

# Monitor vision events (separate terminal)
mosquitto_sub -h localhost -u mqtt_user -P 'Pavan@2005' -t 'etms/vision/#' -v
```

### Terminal 2 — Vision Service (YOLOv8 Camera)
```bash
cd ~/Desktop/Autism/vision-service
conda activate vision-service
python -m src.multi_camera --log-level DEBUG
```

### Terminal 3 — SmartGuard (Anomaly Detection)
```bash
cd ~/Desktop/Autism/smartguard-service
conda activate vision-service
python main.py --mode infer
```

### Terminal 4 — PicoClaw (Go Agent Gateway)
```bash
cd ~/Desktop/Autism/picoclaw
./picoclaw gateway
```

### Terminal 5 — OpenClaw (Emergency Orchestration + Auth0)
```bash
cd ~/Desktop/Autism/openclaw
conda activate openclaw
python run.py
```

### Terminal 6 — Vision Agent (LLM Reasoning)
```bash
cd ~/Desktop/Autism/vision-agent
conda activate vision-agent
python main.py
```

### Terminal 7 — ngrok Tunnel (Twilio Webhooks)
```bash
ngrok http 8200
# Copy the https URL → update openclaw/config/settings.yaml → public_url
```

### Terminal 8 — Home Assistant
```bash
conda run -n homeassistant python -m homeassistant -c ./config
```

---

## Project Overview

ETMS is a production-grade, privacy-first elderly safety system that runs entirely on local edge hardware (Raspberry Pi + ESP32 nodes). When a safety event is detected, Auth0 acts as the security backbone — every AI agent action is **authorized before execution**.

### What Happens When a Fall is Detected

```
Camera (YOLOv8) → Vision Agent (LLM) → MQTT → OpenClaw
                                                    │
                                    ┌───────────────┤
                                    │  Auth0 Chain  │
                                    ├───────────────┤
                                    │ 1. FGA check  │  Is this agent allowed to read fall_events?
                                    │ 2. CIBA push  │  Guardian: approve emergency call? (30s)
                                    │ 3. Token Vault│  Get Slack + Google Calendar tokens
                                    └───────┬───────┘
                                            │
                              ┌─────────────┼─────────────┐
                              ▼             ▼             ▼
                         Slack alert   Calendar event  Twilio call
                         (via Slack    (incident log)  (conversational
                          token)                        AI briefing)
```

---

## Architecture

```
AuthoHackthon/
├── auth0/                    # Auth0 integration layer
│   ├── config.py             # Tenant config, env loader
│   ├── token_vault.py        # Token Vault (Slack + Google Calendar)
│   ├── fga.py                # Fine-Grained Authorization
│   ├── ciba.py               # CIBA backchannel push notifications
│   ├── middleware.py         # JWT verification middleware
│   ├── step_up.py            # Step-up MFA trigger
│   ├── login_helper.py       # OAuth login flow (one-time setup)
│   └── test_full_flow.py     # Integration test suite (18 tests)
│
├── openclaw/                 # Python — Emergency Orchestration Engine
│   ├── run.py                # Entry point
│   ├── main.py               # Core pipeline + Auth0 security chain
│   └── src/
│       ├── action_handlers/  # Telegram, Alexa, Twilio, Slack, Calendar
│       ├── policy_engine.py  # Escalation logic (MONITOR→CRITICAL)
│       ├── incident_manager.py
│       ├── rest_api/         # Flask API + Twilio voice webhooks (port 8200)
│       └── mqtt_bridge.py
│
├── picoclaw/                 # Go — Agent Gateway (Telegram bot)
│   ├── picoclaw              # Binary
│   └── middleware/auth.go    # JWT verification middleware
│
├── vision-service/           # Python — YOLOv8 camera pipeline
├── vision-agent/             # Python — LLM reasoning (Gemini/Ollama)
├── SmartGuard/               # Python — Transformer anomaly detection
├── smartguard-service/       # Python — SmartGuard inference wrapper
├── Elderly-Tracking-System/  # C++/MicroPython — ESP32 hardware nodes
└── guardian-dashboard/       # React — Caregiver consent dashboard
```

---

## Auth0 Features

| Feature | Where Used | What It Does |
|---|---|---|
| **Token Vault** | `auth0/token_vault.py` | Exchanges Auth0 tokens for Slack + Google Calendar tokens — credentials never stored locally |
| **FGA (Fine-Grained Authorization)** | `auth0/fga.py` | Controls which AI agents can access which data streams (VisionAgent → fall_events ✓, health_records ✗) |
| **CIBA (Backchannel Auth)** | `auth0/ciba.py` | Sends push notification to guardian's phone for emergency approval. 30s window: approve → proceed, deny → block, timeout → auto-escalate |
| **JWT Middleware** | `auth0/middleware.py` | Protects OpenClaw REST API routes |
| **Step-Up Auth** | `auth0/step_up.py` | Requires MFA before CRITICAL emergency dispatch |
| **Connected Accounts** | Auth0 Dashboard | Links Google and Slack OAuth accounts to caregiver profile |

### CIBA Authorization Flow
```
Incident detected (HIGH_RISK+)
        │
        ▼
Auth0 CIBA push → Guardian's phone
        │
   ┌────┴────┐
   ▼         ▼         ▼
APPROVE    DENY     (30s timeout)
   │         │          │
proceed   block     AUTO-ESCALATE
emergency emergency  (safety-first)
call      call
```

### Token Vault Strategy (3-tier fallback)
```
Strategy 1: Token Vault exchange endpoint (RFC 8693)
        ↓ (fails on free tier → 401)
Strategy 2: Access token exchange
        ↓ (fails if no caregiver session)
Strategy 3: Management API user identity lookup
        ↓ (always works)
Strategy 3b: Search all users for connection
        + For Google: auto-refresh expired token via upstream OAuth
```

---

## Prerequisites

### System
- Linux (Ubuntu/Debian recommended)
- Python 3.10+ (openclaw), Python 3.13 (auth0 tests)
- Go 1.21+ (picoclaw)
- Conda (environment management)
- Mosquitto MQTT broker
- CUDA GPU (SmartGuard anomaly detection)

### Accounts & Services
- [Auth0](https://auth0.com) account (free tier works)
- [Twilio](https://twilio.com) account (for emergency calls)
- [ngrok](https://ngrok.com) account (for Twilio webhooks)
- Google account (for Calendar integration)
- Slack workspace (for alerts)
- Telegram bot token (for notifications)
- Home Assistant instance (optional, for Alexa/lights/locks)
- Ollama running locally (`qwen2.5:3b` model)

### Python Conda Environments
```bash
# openclaw
conda create -n openclaw python=3.10
conda activate openclaw
pip install -r openclaw/requirements.txt

# vision-service + smartguard
conda create -n vision-service python=3.10
conda activate vision-service
pip install -r vision-service/requirements.txt

# vision-agent
conda create -n vision-agent python=3.10
conda activate vision-agent
pip install -r vision-agent/requirements.txt
```

---

## Environment Setup

### 1. MQTT Broker
```bash
# Install mosquitto
sudo apt install mosquitto mosquitto-clients

# Add user
sudo mosquitto_passwd -c /etc/mosquitto/passwd mqtt_user
# Password: Pavan@2005

# Config: /etc/mosquitto/mosquitto.conf
# allow_anonymous false
# password_file /etc/mosquitto/passwd
# listener 1883

sudo systemctl restart mosquitto
```

### 2. Auth0 Python Dependencies
```bash
cd "/home/pavan/Desktop/Auth0 hackthon/AuthoHackthon"
pip install -r requirements-auth0.txt
```

### 3. Environment Variables
```bash
cp .env.example .env
```

Edit `.env`:
```bash
# Auth0 Core
AUTH0_DOMAIN=your-tenant.auth0.com
AUTH0_CLIENT_ID=...
AUTH0_CLIENT_SECRET=...
AUTH0_AUDIENCE=https://etms-api.example.com

# Auth0 M2M (for Management API)
AUTH0_M2M_CLIENT_ID=...
AUTH0_M2M_CLIENT_SECRET=...

# Auth0 CIBA — caregiver's Auth0 user ID (iss_sub format)
AUTH0_CAREGIVER_ID=google-oauth2|110712621711109566344

# Auth0 FGA
FGA_STORE_ID=...
FGA_API_URL=https://api.us1.fga.dev
FGA_CLIENT_ID=...
FGA_CLIENT_SECRET=...

# Slack bot fallback
SLACK_BOT_TOKEN=xoxb-...
SLACK_DEFAULT_CHANNEL=C0AP7C5QTL7

# Twilio
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+1...
TWILIO_TO_NUMBER=+91...

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=6268065762

# Home Assistant
HA_URL=http://localhost:8123
HA_TOKEN=...

# MQTT
MQTT_BROKER=localhost
MQTT_PORT=1883
MQTT_USER=mqtt_user
MQTT_PASSWORD=Pavan@2005
```

---

## Auth0 Configuration

### 1. Create Auth0 Application
- Type: **Regular Web Application**
- Enable **CIBA** grant type (Dashboard → Applications → Advanced → Grant Types)
- Enable **Token Exchange** grant type

### 2. Create M2M Application
- For Management API access (Token Vault Strategy 3)
- Permissions: `read:users`, `read:user_idp_tokens`, `read:connections`

### 3. Set Up FGA Store
```bash
# Install FGA CLI
# Create store and upload model
fga store create --name "etms"
fga model write --store-id $FGA_STORE_ID --file auth0/fga_model.fga
```

FGA Authorization Model:
```
type user
type data_stream
  relations
    define viewer: [user]

# Vision Agent can view fall events and behavior anomalies
# but NOT health records (privacy boundary)
```

Seed tuples:
```bash
fga tuple write --store-id $FGA_STORE_ID \
  user:vision_agent viewer data_stream:fall_events
fga tuple write --store-id $FGA_STORE_ID \
  user:vision_agent viewer data_stream:behavior_anomalies
fga tuple write --store-id $FGA_STORE_ID \
  user:vision_agent viewer data_stream:location_data
```

### 4. Link Connected Accounts (One-Time)
```bash
cd "/home/pavan/Desktop/Auth0 hackthon/AuthoHackthon"

# Link Google Calendar
python auth0/login_helper.py google
# → Opens browser for Google OAuth
# → Saves token to auth0/.caregiver_token_google

# Link Slack
python auth0/login_helper.py slack
# → Opens browser for Slack OAuth
# → Saves token to auth0/.caregiver_token_slack
```

### 5. Configure Guardian App for CIBA
- Install **Auth0 Guardian** app on caregiver's phone
- Enroll the caregiver account in MFA
- CIBA push notifications will appear when HIGH_RISK+ incidents occur

### 6. Verify Everything
```bash
python auth0/test_full_flow.py
# Expected: 18 passed, 0 failed, 1 skipped
```

---

## Service Reference

### OpenClaw REST API (port 8200)
| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/incidents` | GET | List active incidents |
| `/incidents/<id>` | GET | Incident details |
| `/twilio/voice` | POST | Twilio call webhook (initial briefing) |
| `/twilio/respond` | POST | Twilio speech gather (Q&A) |
| `/api/status` | GET | Auth0-protected status endpoint |

### PicoClaw Gateway (port 18790)
- Telegram bot: `@PicoclowWH_bot`
- Health: `http://localhost:18790/health`

### Twilio Conversational AI
When an emergency call is placed:
1. Twilio calls the elder's family contact
2. OpenClaw speaks an emergency briefing via Amazon Polly (Joanna voice)
3. The responder can ask questions: *"What medications is he on?"*, *"What's his blood type?"*
4. OpenClaw answers from the patient medical profile
5. Multi-turn conversation until the responder says *"goodbye"*

The ngrok URL in `openclaw/config/settings.yaml`:
```yaml
emergency:
  public_url: "https://your-ngrok-url.ngrok-free.dev"
```

---

## How It Works

### Escalation Levels
```
MONITOR → WARNING → HIGH_RISK → CRITICAL
                        │              │
                    CIBA push     Emergency call
                  (30s timeout)   + Slack alert
                                  + Calendar event
                                  + Alexa voice check
```

### Vision Pipeline
```
Camera → YOLOv8 (person detection + pose) → BehaviorAnalyzer
       → SmartGuard (transformer anomaly) → MQTT publish
       → Vision Agent (LLM reasoning: qwen2.5:3b via Ollama)
       → Reasoned event → OpenClaw
```

### CIBA Cooldown
Once a guardian approves an emergency dispatch, subsequent incidents within **5 minutes** reuse the approval silently — no repeated push notifications during a single emergency event.

### Safety-First Principles
- Auth0 failures are **non-blocking** — elderly monitoring is never interrupted by authorization service outages
- CIBA timeout → **auto-escalate** (no response = send help)
- FGA failure → **fail-open** (safety override)
- Every emergency call is placed even if Guardian denies after 30s timeout overrides

---

## Auth0 Test Suite

```bash
python auth0/test_full_flow.py

# Tests:
# ✓ FGA: fall_events access (allowed)
# ✓ FGA: health_records blocked (privacy boundary)
# ✓ FGA: batch stream filter
# ✓ Token Vault: Slack alert delivered
# ✓ Token Vault: Google Calendar event created
# ✓ Token Vault: connected accounts status
# ✓ JWT: invalid token rejected
# ✓ JWT: expired token rejected
# ✓ Step-Up: MFA URL generation
# ✓ Config: all env vars present
# Expected: 18 passed, 0 failed, 1 skipped
```

---

## Hackathon Submission

**Devpost**: Auth0 AI Agents Hackathon — "Authorized to Act"  
**Deadline**: April 6, 2026  
**Category**: AI Agent Security & Authorization

### Judging Criteria Coverage
| Criterion | ETMS Implementation |
|---|---|
| Security Model | Token Vault (Slack + Calendar), FGA (agent boundaries), CIBA (human-in-loop), Step-Up (MFA) |
| User Control | Guardian CIBA push: approve/deny emergency dispatch in real-time |
| Technical Execution | All 4 Auth0 AI features fully implemented, not mocked |
| Design | Multi-service edge AI platform, privacy-first, fail-safe |
| Potential Impact | Real elderly safety system, prevents falls going undetected |
| Novel Insight | CIBA + life-safety: first use of backchannel auth as emergency dispatch gate |

---

## Troubleshooting

**CIBA 400 error**
- `binding_message` must only contain `alphanumerics, whitespace, +-_.,:#`
- Already handled automatically via regex sanitization

**Token Vault 401**
- Strategies 1 & 2 fail on free tier — Strategy 3 (Management API) kicks in automatically
- Ensure M2M app has `read:user_idp_tokens` permission

**Google Calendar 401 (expired token)**
- Token auto-refreshed via upstream OAuth using Auth0-stored refresh token
- Requires Google connection to have `offline_access` scope

**MQTT connection refused**
- `sudo systemctl start mosquitto`
- Check credentials: `mqtt_user` / `Pavan@2005`

**Alexa not responding**
- Check `media_player.alexa_sala_s_echo_dot` entity in Home Assistant
- Ensure HA `REST_API` component is enabled with long-lived token

**ngrok URL changed**
- Update `openclaw/config/settings.yaml` → `emergency.public_url`
- Restart openclaw (`python run.py`)

---

## License

MIT — See individual submodule licenses for third-party components.
