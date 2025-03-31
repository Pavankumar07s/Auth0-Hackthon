# ETMS × Auth0: Authorized to Act
## Hackathon Submission — Auth0 for AI Agents

---

### Project Name
**ETMS Guardian Angel** — Elderly Tracking & Monitoring System with Auth0 AI Agent Security

### Team
**Pawan Kumar** (@Pavankumar07s)

### Repository
https://github.com/Pavankumar07s/Auth0-Hackthon

### Live Demo
- Guardian Dashboard: Deployed on Vercel
- Video Demo: [Link to YouTube/Loom]

---

## What It Does

ETMS is a multi-agent AI system that monitors elderly individuals living independently. It uses edge AI (YOLOv8, transformer autoencoders, ESP32 sensors) to detect falls, anomalies, and health emergencies — then dispatches alerts through Telegram, Slack, Google Calendar, and emergency services.

**The Auth0 integration** makes every agent in this system *authorized* — no AI agent can access data, send alerts, or dispatch emergency services without verified identity, explicit caregiver consent, and real-time authorization checks.

---

## Auth0 Features Implemented

### 1. Token Vault (4 integration points)
**What:** Secure credential exchange for third-party APIs.

**How we use it:**
- Google Calendar: Create medication reminders and incident events
- Slack: Post alerts to #etms-alerts channel
- Token exchange via Auth0 vault — credentials never stored locally
- 3-strategy fallback: refresh token → access token → Management API

**Why it matters:** AI agents calling external APIs is a major credential leakage risk. Token Vault eliminates this entirely — agents present their Auth0 identity, and the vault provides scoped, short-lived third-party tokens.

**Files:** `auth0/token_vault.py`, `openclaw/auth0_actions.py`

### 2. CIBA — Client-Initiated Backchannel Authentication
**What:** Async push-based authorization for critical decisions.

**How we use it:**
- Fall detected → CIBA push notification sent to caregiver's phone (Galaxy A52s 5G)
- Caregiver approves/denies via Auth0 Guardian app
- Emergency dispatch ONLY proceeds after explicit approval
- 120-second timeout with exponential backoff polling

**Why it matters:** This is the novel insight — in life-safety systems, **the AI should not make irreversible decisions alone**. CIBA provides async human-in-the-loop authorization that doesn't require the human to be at a computer.

**Files:** `auth0/ciba.py`, `openclaw/auth0_actions.py`

### 3. Fine-Grained Authorization (FGA)
**What:** Zanzibar-based per-resource permission model.

**How we use it:**
- Authorization model: `data_stream` type with `viewer` relation
- Vision Agent can only access camera feeds if `vision_agent is viewer on data_stream:vision_feed`
- Caregiver controls permissions from Guardian Dashboard
- FGA checks happen BEFORE data retrieval, not after

**Why it matters:** AI agents shouldn't access all available data by default. FGA implements least-privilege for every data stream — caregivers explicitly grant and revoke access per-stream.

**Files:** `auth0/fga.py`, `auth0/fga_setup.py`, `vision-agent/fga_retriever.py`

### 4. JWT Middleware
**What:** RS256 token validation on all API endpoints.

**How we use it:**
- FastAPI middleware for new Python endpoints
- Flask decorator for existing OpenClaw REST API
- Go middleware for PicoClaw agent endpoints
- Scope-based access control: `read:incidents`, `write:consent`, `dispatch:emergency`

**Files:** `auth0/middleware.py`, `openclaw/api_auth.py`, `picoclaw/middleware/auth.go`

### 5. Step-Up Authentication
**What:** Require additional MFA before high-risk operations.

**How we use it:**
- Before emergency services dispatch, check token's ACR claim
- If caregiver hasn't recently completed MFA → require step-up
- Integrates with CIBA flow for full critical dispatch chain

**Files:** `auth0/step_up.py`

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                  Guardian Dashboard                   │
│             (React / Next.js / Vercel)                │
│  Consent Panel │ Audit Log │ Token Status │ Incidents │
└──────────────────────┬───────────────────────────────┘
                       │ Auth0 JWT
┌──────────────────────▼───────────────────────────────┐
│                   OpenClaw API                        │
│              (Flask + Auth0 JWT Middleware)            │
│     PolicyEngine │ IncidentManager │ Auth0Actions     │
└────────┬─────────────────┬───────────────┬───────────┘
         │                 │               │
    ┌────▼────┐     ┌──────▼──────┐  ┌─────▼─────┐
    │  FGA    │     │ Token Vault │  │   CIBA    │
    │ Check   │     │  Exchange   │  │   Push    │
    └────┬────┘     └──────┬──────┘  └─────┬─────┘
         │                 │               │
    ┌────▼────┐     ┌──────▼──────┐  ┌─────▼─────┐
    │ Vision  │     │   Slack /   │  │ Guardian  │
    │ Agent   │     │  Calendar   │  │   App     │
    └─────────┘     └─────────────┘  └───────────┘
```

---

## Security Model

| Layer | Auth0 Feature | What It Protects |
|-------|--------------|-----------------|
| Data Access | FGA | Camera feeds, health data, location — per-stream consent |
| API Gateway | JWT + Scopes | All REST endpoints — role-based access |
| Third-Party Calls | Token Vault | Google Calendar, Slack — zero local credential storage |
| Critical Decisions | CIBA | Emergency dispatch — async caregiver approval |
| High-Risk Actions | Step-Up Auth | Emergency services — MFA-verified authorization |

---

## User Control: Guardian Dashboard

The Guardian Dashboard gives caregivers complete visibility and control:

- **Consent Panel**: Toggle FGA permissions per data stream (writes relationship tuples in real-time)
- **Token Status**: See which third-party integrations are connected via Token Vault, with revoke capability
- **Audit Log**: Complete record of every Auth0 security decision (FGA checks, CIBA approvals, token exchanges)
- **Incident Feed**: Real-time safety alerts with Auth0 actions shown for each incident
- **Auth0 Features**: Educational panel explaining how each Auth0 feature protects their loved one

---

## Novel Insight: CIBA + Life-Safety Pattern

The most innovative aspect of this project is the **CIBA + life-safety pattern**:

Traditional life-safety systems face a dilemma:
1. **Auto-dispatch**: Fast but causes false-positive emergency calls (elderly people get traumatized)
2. **Manual confirmation**: Safer but requires the caregiver to be watching a screen

**CIBA solves this** by providing async push-based authorization:
- The AI detects the emergency
- A push notification goes to the caregiver's phone (not a computer screen)
- The caregiver approves or denies from wherever they are
- The system proceeds only with explicit consent

This pattern is generalizable to any AI system where:
- Decisions are consequential and potentially irreversible
- A human authority exists but isn't always available at a terminal
- Speed matters but not at the cost of autonomy

---

## Technical Stack

| Component | Technology | Auth0 Integration |
|-----------|-----------|------------------|
| Vision Agent | Python, YOLOv8 | FGA-filtered data retrieval |
| SmartGuard | Python, Transformer Autoencoder | Anomaly scores feed into Auth0-protected pipeline |
| OpenClaw | Python, Flask | JWT middleware, Auth0 action handlers |
| PicoClaw | Go | JWT middleware for agent endpoints |
| IoT Nodes | C++, ESP32 | MQTT → Auth0-protected API |
| Dashboard | React, Next.js, Tailwind | Auth0 Universal Login |

---

## Testing

18/18 tests passing across all Auth0 features:
- Token Vault: JWT decode, refresh token loading, connection status
- CIBA: Import validation, constant verification, binding message generation
- FGA: Authorization model, relationship tuples, batch checks
- JWT: Token rejection (invalid signature, expired, wrong audience)
- Step-Up: ACR claim verification, authorization URL generation
- Config: Environment variable validation

---

## Running the Project

```bash
# Install Auth0 dependencies
pip install -r requirements-auth0.txt

# Set environment variables
cp .env.example .env
# Fill in your Auth0 credentials

# Run tests
python auth0/test_full_flow.py

# Start OpenClaw API with Auth0 protection
cd openclaw && python main.py

# Start Guardian Dashboard
cd guardian-dashboard && npm install && npm run dev
```

---

## Blog Post

See [MEDIUM_ARTICLE_UPDATED.md](../MEDIUM_ARTICLE_UPDATED.md) for the full technical blog post covering the CIBA + life-safety pattern innovation.

---

## License

MIT

---

*Built with ❤️ for elderly independence and AI safety.*
*Secured with Auth0 — because AI agents must be authorized to act.*
