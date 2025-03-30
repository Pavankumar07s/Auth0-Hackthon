"use client";

import { motion } from "framer-motion";
import { Shield, Lock, Bell, Eye, Fingerprint, Key, ArrowRight, ExternalLink } from "lucide-react";

interface AuthFeature {
  id: string;
  title: string;
  description: string;
  howUsed: string;
  icon: React.ReactNode;
  color: string;
  bg: string;
  docsUrl: string;
}

const auth0Features: AuthFeature[] = [
  {
    id: "token-vault",
    title: "Token Vault",
    description:
      "Securely exchange Auth0 tokens for third-party API credentials without ever storing them locally.",
    howUsed:
      "ETMS agents exchange their Auth0 access tokens for Google Calendar and Slack tokens on-demand. The vault manages token lifecycle, refresh, and revocation. Credentials never touch the agent's memory.",
    icon: <Key className="w-6 h-6" />,
    color: "text-purple-400",
    bg: "bg-purple-500/10",
    docsUrl: "https://github.com/auth0/auth0-ai-python/tree/main/examples/token-vault",
  },
  {
    id: "ciba",
    title: "CIBA (Backchannel Auth)",
    description:
      "Async push-based authorization flow that requires caregiver approval before critical actions.",
    howUsed:
      "When a critical fall is detected, ETMS sends a CIBA backchannel request. The caregiver receives a push notification on their Galaxy A52s 5G via Auth0 Guardian. Emergency dispatch only proceeds after explicit approval.",
    icon: <Bell className="w-6 h-6" />,
    color: "text-guardian-amber",
    bg: "bg-guardian-amber/10",
    docsUrl: "https://github.com/auth0/auth0-ai-python/tree/main/examples/async-authorization",
  },
  {
    id: "fga",
    title: "Fine-Grained Authorization",
    description:
      "Zanzibar-based permission model controlling exactly which agents can access which data streams.",
    howUsed:
      "Before the Vision Agent can process camera feeds, FGA verifies the relationship tuple 'vision_agent is viewer on data_stream:vision_feed'. Caregivers manage these permissions from the Consent Panel. Every access is checked in real-time.",
    icon: <Shield className="w-6 h-6" />,
    color: "text-guardian-teal",
    bg: "bg-guardian-teal/10",
    docsUrl: "https://github.com/auth0/auth0-ai-python/tree/main/examples/authorization-for-rag",
  },
  {
    id: "jwt",
    title: "JWT Middleware",
    description:
      "RS256 token validation protecting all ETMS API endpoints with scope-based access control.",
    howUsed:
      "Every API request to OpenClaw and the vision service is validated against Auth0's JWKS endpoint. Tokens must have the correct issuer, audience, and scopes (read:incidents, write:consent, dispatch:emergency).",
    icon: <Lock className="w-6 h-6" />,
    color: "text-guardian-blue",
    bg: "bg-guardian-blue/10",
    docsUrl: "https://auth0.com/docs/secure/tokens/json-web-tokens",
  },
  {
    id: "step-up",
    title: "Step-Up Authentication",
    description:
      "Require additional MFA verification before executing high-risk emergency operations.",
    howUsed:
      "Before dispatching emergency services, ETMS checks the token's ACR claim for multi-factor authentication. If the caregiver hasn't recently completed MFA, they're prompted to re-authenticate with a stronger factor.",
    icon: <Fingerprint className="w-6 h-6" />,
    color: "text-guardian-red",
    bg: "bg-guardian-red/10",
    docsUrl: "https://auth0.com/docs/secure/multi-factor-authentication/step-up-authentication",
  },
  {
    id: "audit",
    title: "Security Audit Trail",
    description:
      "Complete record of every Auth0 security decision for compliance and transparency.",
    howUsed:
      "Every FGA check, Token Vault exchange, CIBA approval, and JWT validation is logged with timestamps, actors, and outcomes. Caregivers can review the full audit trail from the dashboard.",
    icon: <Eye className="w-6 h-6" />,
    color: "text-guardian-green",
    bg: "bg-guardian-green/10",
    docsUrl: "https://auth0.com/docs/deploy-monitor/logs",
  },
];

export default function Auth0Features() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-guardian-text flex items-center gap-2">
          <Shield className="w-5 h-5 text-guardian-teal" />
          Auth0 AI Agent Security Stack
        </h2>
        <p className="text-sm text-guardian-muted mt-1">
          Every Auth0 feature used in ETMS and how it protects your loved one.
        </p>
      </div>

      {/* Architecture Flow */}
      <div className="p-4 rounded-xl bg-gradient-to-r from-guardian-teal/5 to-guardian-blue/5 border border-guardian-teal/20">
        <h3 className="text-sm font-semibold mb-3">Security Flow: Fall Detection → Emergency Dispatch</h3>
        <div className="flex items-center gap-2 flex-wrap text-xs text-guardian-muted">
          <span className="px-2 py-1 rounded bg-guardian-card border border-guardian-border">
            📷 Camera Feed
          </span>
          <ArrowRight className="w-3 h-3 text-guardian-muted" />
          <span className="px-2 py-1 rounded bg-guardian-teal/10 border border-guardian-teal/30 text-guardian-teal">
            FGA Check
          </span>
          <ArrowRight className="w-3 h-3 text-guardian-muted" />
          <span className="px-2 py-1 rounded bg-guardian-card border border-guardian-border">
            🤖 Vision Agent
          </span>
          <ArrowRight className="w-3 h-3 text-guardian-muted" />
          <span className="px-2 py-1 rounded bg-guardian-amber/10 border border-guardian-amber/30 text-guardian-amber">
            CIBA Push
          </span>
          <ArrowRight className="w-3 h-3 text-guardian-muted" />
          <span className="px-2 py-1 rounded bg-guardian-red/10 border border-guardian-red/30 text-guardian-red">
            Step-Up MFA
          </span>
          <ArrowRight className="w-3 h-3 text-guardian-muted" />
          <span className="px-2 py-1 rounded bg-purple-500/10 border border-purple-500/30 text-purple-400">
            Token Vault
          </span>
          <ArrowRight className="w-3 h-3 text-guardian-muted" />
          <span className="px-2 py-1 rounded bg-guardian-card border border-guardian-border">
            🚨 Dispatch
          </span>
        </div>
      </div>

      {/* Feature Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {auth0Features.map((feature, index) => (
          <motion.div
            key={feature.id}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: index * 0.1 }}
            className="p-5 rounded-xl border border-guardian-border bg-guardian-card hover:border-guardian-teal/30 transition-all"
          >
            <div className="flex items-start gap-3 mb-3">
              <div className={`p-2 rounded-lg ${feature.bg}`}>
                <span className={feature.color}>{feature.icon}</span>
              </div>
              <div>
                <h3 className="font-semibold text-sm text-guardian-text">{feature.title}</h3>
                <p className="text-xs text-guardian-muted mt-0.5">{feature.description}</p>
              </div>
            </div>
            <div className="p-3 rounded-lg bg-guardian-bg/50 border border-guardian-border mb-3">
              <span className="text-[10px] font-semibold text-guardian-teal uppercase tracking-wider block mb-1">
                How ETMS Uses This
              </span>
              <p className="text-xs text-guardian-muted leading-relaxed">{feature.howUsed}</p>
            </div>
            <a
              href={feature.docsUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-guardian-teal hover:underline"
            >
              <ExternalLink className="w-3 h-3" />
              Documentation
            </a>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
