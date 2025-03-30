"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Eye, Shield, Bell, Lock, Clock, Filter, ChevronDown } from "lucide-react";

interface AuditEntry {
  id: string;
  timestamp: string;
  action: string;
  actor: string;
  auth0Feature: string;
  details: string;
  severity: "info" | "warning" | "critical";
}

const mockAuditLog: AuditEntry[] = [
  {
    id: "1",
    timestamp: "2025-03-30T14:32:00Z",
    action: "CIBA Push Approved",
    actor: "caregiver@example.com",
    auth0Feature: "CIBA",
    details: "Emergency dispatch authorization approved via Guardian push on Galaxy A52s 5G",
    severity: "critical",
  },
  {
    id: "2",
    timestamp: "2025-03-30T14:31:45Z",
    action: "Step-Up Auth Triggered",
    actor: "system",
    auth0Feature: "Step-Up",
    details: "MFA step-up required for emergency dispatch. ACR: http://schemas.openid.net/pape/policies/2007/06/multi-factor",
    severity: "critical",
  },
  {
    id: "3",
    timestamp: "2025-03-30T14:30:12Z",
    action: "Fall Detected - Severity HIGH",
    actor: "vision_agent",
    auth0Feature: "FGA",
    details: "FGA check passed: vision_agent is viewer on data_stream:vision_feed. Incident created.",
    severity: "warning",
  },
  {
    id: "4",
    timestamp: "2025-03-30T14:25:00Z",
    action: "Slack Alert Sent",
    actor: "token_vault",
    auth0Feature: "Token Vault",
    details: "Token exchanged via Token Vault for Slack connection. Alert posted to #etms-alerts channel.",
    severity: "info",
  },
  {
    id: "5",
    timestamp: "2025-03-30T14:20:00Z",
    action: "Calendar Event Created",
    actor: "token_vault",
    auth0Feature: "Token Vault",
    details: "Token exchanged for Google Calendar. Medication reminder created for 3:00 PM.",
    severity: "info",
  },
  {
    id: "6",
    timestamp: "2025-03-30T13:00:00Z",
    action: "FGA Consent Updated",
    actor: "caregiver@example.com",
    auth0Feature: "FGA",
    details: "Relationship tuple added: caregiver is viewer on data_stream:health_telemetry",
    severity: "info",
  },
  {
    id: "7",
    timestamp: "2025-03-30T12:55:00Z",
    action: "JWT Token Validated",
    actor: "api_gateway",
    auth0Feature: "JWT",
    details: "Access token validated. Scopes: read:incidents write:consent. Issuer: dev-pavankumar.us.auth0.com",
    severity: "info",
  },
  {
    id: "8",
    timestamp: "2025-03-30T12:00:00Z",
    action: "System Boot - Auth0 Connected",
    actor: "system",
    auth0Feature: "Config",
    details: "All Auth0 services initialized: Token Vault, CIBA, FGA, JWT middleware, Step-Up auth.",
    severity: "info",
  },
];

export default function AuditLog() {
  const [filter, setFilter] = useState<string>("all");
  const [expanded, setExpanded] = useState<string | null>(null);

  const features = ["all", "CIBA", "Token Vault", "FGA", "JWT", "Step-Up", "Config"];

  const filtered =
    filter === "all"
      ? mockAuditLog
      : mockAuditLog.filter((e) => e.auth0Feature === filter);

  const severityStyles = {
    info: "border-guardian-blue/30 bg-guardian-blue/5",
    warning: "border-guardian-amber/30 bg-guardian-amber/5",
    critical: "border-guardian-red/30 bg-guardian-red/5",
  };

  const severityDot = {
    info: "bg-guardian-blue",
    warning: "bg-guardian-amber",
    critical: "bg-guardian-red animate-pulse-glow",
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-guardian-text flex items-center gap-2">
          <Eye className="w-5 h-5 text-guardian-teal" />
          Auth0 Audit Trail
        </h2>
        <p className="text-sm text-guardian-muted mt-1">
          Complete record of all Auth0 security events — CIBA approvals, Token Vault exchanges,
          FGA checks, JWT validations, and step-up triggers.
        </p>
      </div>

      {/* Filter Bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <Filter className="w-4 h-4 text-guardian-muted" />
        {features.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
              filter === f
                ? "bg-guardian-teal/20 text-guardian-teal border border-guardian-teal/30"
                : "bg-guardian-card text-guardian-muted border border-guardian-border hover:text-guardian-text"
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      {/* Timeline */}
      <div className="space-y-3">
        {filtered.map((entry, index) => (
          <motion.div
            key={entry.id}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: index * 0.05 }}
            className={`p-4 rounded-xl border ${severityStyles[entry.severity]} cursor-pointer`}
            onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-3">
                <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${severityDot[entry.severity]}`} />
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-sm text-guardian-text">{entry.action}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-guardian-card border border-guardian-border text-guardian-muted font-mono">
                      {entry.auth0Feature}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <Clock className="w-3 h-3 text-guardian-muted" />
                    <span className="text-xs text-guardian-muted">
                      {new Date(entry.timestamp).toLocaleString()}
                    </span>
                    <span className="text-xs text-guardian-muted">• {entry.actor}</span>
                  </div>
                </div>
              </div>
              <ChevronDown
                className={`w-4 h-4 text-guardian-muted transition-transform ${
                  expanded === entry.id ? "rotate-180" : ""
                }`}
              />
            </div>
            {expanded === entry.id && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                className="mt-3 pt-3 border-t border-guardian-border"
              >
                <p className="text-xs text-guardian-muted leading-relaxed">{entry.details}</p>
              </motion.div>
            )}
          </motion.div>
        ))}
      </div>
    </div>
  );
}
