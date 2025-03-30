"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Bell, AlertTriangle, Activity, CheckCircle, Clock, MapPin, Camera, Heart } from "lucide-react";

interface Incident {
  id: string;
  timestamp: string;
  type: "fall" | "anomaly" | "geofence" | "vitals" | "inactivity";
  severity: "low" | "medium" | "high" | "critical";
  title: string;
  description: string;
  auth0Actions: string[];
  status: "active" | "resolved" | "pending_approval";
  location?: string;
}

const mockIncidents: Incident[] = [
  {
    id: "inc-001",
    timestamp: "2025-03-30T14:30:12Z",
    type: "fall",
    severity: "critical",
    title: "Fall Detected — Living Room",
    description: "YOLOv8 pose estimation detected sudden vertical displacement. Person on ground for > 10 seconds. SmartGuard anomaly score: 0.94.",
    auth0Actions: [
      "FGA: vision_agent authorized to access vision_feed",
      "CIBA: Push notification sent to caregiver",
      "Step-Up: MFA required for emergency dispatch",
      "Token Vault: Slack alert sent via vault-managed token",
    ],
    status: "pending_approval",
    location: "Living Room Camera 1",
  },
  {
    id: "inc-002",
    timestamp: "2025-03-30T12:15:00Z",
    type: "anomaly",
    severity: "medium",
    title: "Unusual Activity Pattern",
    description: "SmartGuard transformer detected deviation from normal daily routine. Extended period in bathroom (45 min vs 15 min average).",
    auth0Actions: [
      "FGA: vision_agent authorized for health_telemetry",
      "Token Vault: Calendar check for medical appointments",
    ],
    status: "resolved",
    location: "Bathroom Sensor",
  },
  {
    id: "inc-003",
    timestamp: "2025-03-30T10:30:00Z",
    type: "vitals",
    severity: "high",
    title: "Heart Rate Anomaly",
    description: "Wearable sensor reported heart rate spike to 120 BPM while at rest. Duration: 3 minutes.",
    auth0Actions: [
      "FGA: caregiver authorized for health_telemetry",
      "Token Vault: Google Calendar medication reminder created",
      "Slack notification sent to #etms-alerts",
    ],
    status: "resolved",
    location: "Wearable Sensor",
  },
  {
    id: "inc-004",
    timestamp: "2025-03-30T08:00:00Z",
    type: "inactivity",
    severity: "low",
    title: "Morning Routine Delayed",
    description: "No movement detected in kitchen by 8:00 AM. Normal breakfast time is 7:00-7:30 AM.",
    auth0Actions: [
      "FGA: vision_agent checked location stream",
    ],
    status: "resolved",
    location: "Kitchen Sensor",
  },
  {
    id: "inc-005",
    timestamp: "2025-03-29T16:45:00Z",
    type: "geofence",
    severity: "medium",
    title: "Geofence Exit",
    description: "GPS indicates person has left the safe zone perimeter. Duration outside: 12 minutes.",
    auth0Actions: [
      "FGA: location stream access verified",
      "Token Vault: Telegram alert sent",
    ],
    status: "resolved",
    location: "Outdoor GPS",
  },
];

export default function IncidentFeed() {
  const [incidents] = useState<Incident[]>(mockIncidents);
  const [filter, setFilter] = useState<string>("all");

  const typeConfig = {
    fall: { icon: <AlertTriangle className="w-4 h-4" />, color: "text-guardian-red" },
    anomaly: { icon: <Activity className="w-4 h-4" />, color: "text-guardian-amber" },
    geofence: { icon: <MapPin className="w-4 h-4" />, color: "text-guardian-blue" },
    vitals: { icon: <Heart className="w-4 h-4" />, color: "text-guardian-red" },
    inactivity: { icon: <Clock className="w-4 h-4" />, color: "text-guardian-muted" },
  };

  const severityColors = {
    low: "border-guardian-border bg-guardian-card",
    medium: "border-guardian-amber/30 bg-guardian-amber/5",
    high: "border-guardian-red/20 bg-guardian-red/5",
    critical: "border-guardian-red/40 bg-guardian-red/10",
  };

  const statusBadge = {
    active: { label: "Active", bg: "bg-guardian-red/20 text-guardian-red" },
    resolved: { label: "Resolved", bg: "bg-guardian-green/20 text-guardian-green" },
    pending_approval: { label: "Awaiting CIBA Approval", bg: "bg-guardian-amber/20 text-guardian-amber" },
  };

  const filtered =
    filter === "all"
      ? incidents
      : incidents.filter((i) => i.type === filter);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-guardian-text flex items-center gap-2">
          <Bell className="w-5 h-5 text-guardian-teal" />
          Incident Feed
        </h2>
        <p className="text-sm text-guardian-muted mt-1">
          Real-time safety incidents with Auth0 security actions applied at every step.
        </p>
      </div>

      {/* Filters */}
      <div className="flex gap-2 flex-wrap">
        {["all", "fall", "anomaly", "vitals", "geofence", "inactivity"].map((t) => (
          <button
            key={t}
            onClick={() => setFilter(t)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-all capitalize ${
              filter === t
                ? "bg-guardian-teal/20 text-guardian-teal border border-guardian-teal/30"
                : "bg-guardian-card text-guardian-muted border border-guardian-border hover:text-guardian-text"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Incident List */}
      <div className="space-y-4">
        <AnimatePresence>
          {filtered.map((incident, index) => (
            <motion.div
              key={incident.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ delay: index * 0.05 }}
              className={`p-4 rounded-xl border ${severityColors[incident.severity]}`}
            >
              {/* Header */}
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="flex items-start gap-3">
                  <div className={`mt-0.5 ${typeConfig[incident.type].color}`}>
                    {typeConfig[incident.type].icon}
                  </div>
                  <div>
                    <h3 className="font-semibold text-sm text-guardian-text">{incident.title}</h3>
                    <div className="flex items-center gap-2 mt-0.5">
                      <Clock className="w-3 h-3 text-guardian-muted" />
                      <span className="text-xs text-guardian-muted">
                        {new Date(incident.timestamp).toLocaleString()}
                      </span>
                      {incident.location && (
                        <>
                          <Camera className="w-3 h-3 text-guardian-muted" />
                          <span className="text-xs text-guardian-muted">{incident.location}</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
                <span
                  className={`text-[10px] px-2 py-1 rounded-full font-medium whitespace-nowrap ${
                    statusBadge[incident.status].bg
                  }`}
                >
                  {statusBadge[incident.status].label}
                </span>
              </div>

              {/* Description */}
              <p className="text-xs text-guardian-muted leading-relaxed mb-3">
                {incident.description}
              </p>

              {/* Auth0 Actions */}
              <div className="space-y-1.5">
                <span className="text-[10px] font-semibold text-guardian-teal uppercase tracking-wider">
                  Auth0 Security Actions
                </span>
                {incident.auth0Actions.map((action, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <CheckCircle className="w-3 h-3 text-guardian-green shrink-0" />
                    <span className="text-[11px] text-guardian-muted">{action}</span>
                  </div>
                ))}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
