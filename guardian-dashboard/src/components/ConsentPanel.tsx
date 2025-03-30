"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Shield, ToggleLeft, ToggleRight, AlertTriangle, CheckCircle, Info } from "lucide-react";

interface ConsentItem {
  id: string;
  label: string;
  description: string;
  category: "data" | "action" | "emergency";
  enabled: boolean;
  fgaRelation: string;
}

const defaultConsents: ConsentItem[] = [
  {
    id: "vision_stream",
    label: "Vision Stream Access",
    description: "Allow AI agents to process camera feeds for fall detection and pose analysis",
    category: "data",
    enabled: true,
    fgaRelation: "viewer on data_stream:vision_feed",
  },
  {
    id: "health_telemetry",
    label: "Health Telemetry",
    description: "Allow access to heart rate, SpO2, and movement data from wearable sensors",
    category: "data",
    enabled: true,
    fgaRelation: "viewer on data_stream:health_telemetry",
  },
  {
    id: "location_data",
    label: "Location Tracking",
    description: "Allow GPS and indoor positioning data to be processed for geofence alerts",
    category: "data",
    enabled: true,
    fgaRelation: "viewer on data_stream:location",
  },
  {
    id: "emergency_dispatch",
    label: "Emergency Auto-Dispatch",
    description: "Allow system to contact emergency services automatically upon critical fall detection",
    category: "emergency",
    enabled: false,
    fgaRelation: "dispatcher on action:emergency_call",
  },
  {
    id: "calendar_events",
    label: "Calendar Event Creation",
    description: "Allow agent to create medication reminders and check-in events via Google Calendar",
    category: "action",
    enabled: true,
    fgaRelation: "writer on integration:google_calendar",
  },
  {
    id: "slack_alerts",
    label: "Slack Notifications",
    description: "Allow agent to send alert notifications to your connected Slack workspace",
    category: "action",
    enabled: true,
    fgaRelation: "writer on integration:slack",
  },
];

export default function ConsentPanel() {
  const [consents, setConsents] = useState<ConsentItem[]>(defaultConsents);
  const [saving, setSaving] = useState<string | null>(null);

  const toggleConsent = async (id: string) => {
    setSaving(id);
    // Simulate FGA write
    await new Promise((r) => setTimeout(r, 800));
    setConsents((prev) =>
      prev.map((c) => (c.id === id ? { ...c, enabled: !c.enabled } : c))
    );
    setSaving(null);
  };

  const categoryConfig = {
    data: { label: "Data Access", color: "text-guardian-blue", bg: "bg-guardian-blue/10" },
    action: { label: "Agent Actions", color: "text-guardian-teal", bg: "bg-guardian-teal/10" },
    emergency: { label: "Emergency", color: "text-guardian-red", bg: "bg-guardian-red/10" },
  };

  const grouped = {
    data: consents.filter((c) => c.category === "data"),
    action: consents.filter((c) => c.category === "action"),
    emergency: consents.filter((c) => c.category === "emergency"),
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-guardian-text flex items-center gap-2">
            <Shield className="w-5 h-5 text-guardian-teal" />
            Consent Management
          </h2>
          <p className="text-sm text-guardian-muted mt-1">
            Control what your AI agents can access. Powered by Auth0 Fine-Grained Authorization (FGA).
          </p>
        </div>
      </div>

      <div className="p-3 rounded-lg bg-guardian-teal/5 border border-guardian-teal/20 flex items-start gap-3">
        <Info className="w-5 h-5 text-guardian-teal shrink-0 mt-0.5" />
        <p className="text-xs text-guardian-muted">
          Each toggle writes a relationship tuple to your Auth0 FGA store. Changes take effect immediately
          across all ETMS agents. The relationship model uses the Zanzibar authorization pattern.
        </p>
      </div>

      {(["data", "action", "emergency"] as const).map((category) => (
        <div key={category}>
          <div className="flex items-center gap-2 mb-3">
            <span
              className={`text-xs font-semibold px-2 py-1 rounded ${categoryConfig[category].bg} ${categoryConfig[category].color}`}
            >
              {categoryConfig[category].label}
            </span>
          </div>
          <div className="space-y-3">
            {grouped[category].map((consent) => (
              <motion.div
                key={consent.id}
                layout
                className="p-4 rounded-xl border border-guardian-border bg-guardian-card flex items-center justify-between gap-4"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-sm text-guardian-text">{consent.label}</span>
                    {consent.category === "emergency" && !consent.enabled && (
                      <AlertTriangle className="w-3.5 h-3.5 text-guardian-amber" />
                    )}
                    {consent.enabled && (
                      <CheckCircle className="w-3.5 h-3.5 text-guardian-green" />
                    )}
                  </div>
                  <p className="text-xs text-guardian-muted">{consent.description}</p>
                  <code className="text-[10px] text-guardian-muted/60 mt-1 block font-mono">
                    FGA: {consent.fgaRelation}
                  </code>
                </div>
                <button
                  onClick={() => toggleConsent(consent.id)}
                  disabled={saving === consent.id}
                  className="shrink-0"
                >
                  {saving === consent.id ? (
                    <div className="w-10 h-6 flex items-center justify-center">
                      <div className="w-4 h-4 border-2 border-guardian-teal border-t-transparent rounded-full animate-spin" />
                    </div>
                  ) : consent.enabled ? (
                    <ToggleRight className="w-10 h-6 text-guardian-teal" />
                  ) : (
                    <ToggleLeft className="w-10 h-6 text-guardian-muted" />
                  )}
                </button>
              </motion.div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
