"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Lock, RefreshCw, Unlink, CheckCircle, XCircle, Clock, ExternalLink } from "lucide-react";

interface TokenConnection {
  id: string;
  provider: string;
  providerIcon: string;
  status: "connected" | "disconnected" | "expired";
  lastUsed: string | null;
  scopes: string[];
  vaultManaged: boolean;
}

const mockConnections: TokenConnection[] = [
  {
    id: "google",
    provider: "Google Calendar",
    providerIcon: "🗓️",
    status: "connected",
    lastUsed: "2025-03-30T14:20:00Z",
    scopes: ["https://www.googleapis.com/auth/calendar"],
    vaultManaged: true,
  },
  {
    id: "slack",
    provider: "Slack",
    providerIcon: "💬",
    status: "connected",
    lastUsed: "2025-03-30T14:25:00Z",
    scopes: ["chat:write", "channels:read"],
    vaultManaged: true,
  },
  {
    id: "telegram",
    provider: "Telegram Bot",
    providerIcon: "✈️",
    status: "connected",
    lastUsed: "2025-03-30T14:30:00Z",
    scopes: ["send_message"],
    vaultManaged: false,
  },
  {
    id: "homeassistant",
    provider: "Home Assistant",
    providerIcon: "🏠",
    status: "disconnected",
    lastUsed: null,
    scopes: ["api:*"],
    vaultManaged: false,
  },
];

export default function TokenStatus() {
  const [connections, setConnections] = useState<TokenConnection[]>(mockConnections);
  const [refreshing, setRefreshing] = useState<string | null>(null);

  const handleRefresh = async (id: string) => {
    setRefreshing(id);
    await new Promise((r) => setTimeout(r, 1200));
    setConnections((prev) =>
      prev.map((c) =>
        c.id === id ? { ...c, status: "connected", lastUsed: new Date().toISOString() } : c
      )
    );
    setRefreshing(null);
  };

  const handleRevoke = async (id: string) => {
    setRefreshing(id);
    await new Promise((r) => setTimeout(r, 800));
    setConnections((prev) =>
      prev.map((c) => (c.id === id ? { ...c, status: "disconnected", lastUsed: null } : c))
    );
    setRefreshing(null);
  };

  const statusConfig = {
    connected: {
      icon: <CheckCircle className="w-4 h-4 text-guardian-green" />,
      label: "Connected",
      color: "text-guardian-green",
      bg: "bg-guardian-green/10",
    },
    disconnected: {
      icon: <XCircle className="w-4 h-4 text-guardian-muted" />,
      label: "Disconnected",
      color: "text-guardian-muted",
      bg: "bg-guardian-card",
    },
    expired: {
      icon: <Clock className="w-4 h-4 text-guardian-amber" />,
      label: "Expired",
      color: "text-guardian-amber",
      bg: "bg-guardian-amber/10",
    },
  };

  const vaultConnections = connections.filter((c) => c.vaultManaged);
  const otherConnections = connections.filter((c) => !c.vaultManaged);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-guardian-text flex items-center gap-2">
          <Lock className="w-5 h-5 text-guardian-teal" />
          Token Vault Status
        </h2>
        <p className="text-sm text-guardian-muted mt-1">
          Auth0 Token Vault securely manages third-party credentials. Tokens are never stored locally —
          they&apos;re exchanged on-demand via the vault.
        </p>
      </div>

      {/* Vault Info Banner */}
      <div className="p-4 rounded-xl bg-gradient-to-r from-guardian-teal/10 to-guardian-blue/10 border border-guardian-teal/20">
        <div className="flex items-start gap-3">
          <Lock className="w-5 h-5 text-guardian-teal shrink-0 mt-0.5" />
          <div>
            <h3 className="text-sm font-semibold text-guardian-text">How Token Vault Works</h3>
            <p className="text-xs text-guardian-muted mt-1 leading-relaxed">
              When an ETMS agent needs to call Google Calendar or Slack, it presents its Auth0 access token
              to the Token Vault. The vault exchanges it for a scoped third-party token, which is used once
              and never persisted. This eliminates credential leakage risk.
            </p>
          </div>
        </div>
      </div>

      {/* Token Vault Managed */}
      <div>
        <h3 className="text-sm font-semibold text-guardian-muted mb-3 flex items-center gap-2">
          <Lock className="w-3.5 h-3.5" /> Token Vault Managed
        </h3>
        <div className="space-y-3">
          {vaultConnections.map((conn) => (
            <ConnectionCard
              key={conn.id}
              connection={conn}
              statusConfig={statusConfig[conn.status]}
              refreshing={refreshing === conn.id}
              onRefresh={() => handleRefresh(conn.id)}
              onRevoke={() => handleRevoke(conn.id)}
            />
          ))}
        </div>
      </div>

      {/* Other Connections */}
      <div>
        <h3 className="text-sm font-semibold text-guardian-muted mb-3">Other Integrations</h3>
        <div className="space-y-3">
          {otherConnections.map((conn) => (
            <ConnectionCard
              key={conn.id}
              connection={conn}
              statusConfig={statusConfig[conn.status]}
              refreshing={refreshing === conn.id}
              onRefresh={() => handleRefresh(conn.id)}
              onRevoke={() => handleRevoke(conn.id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ConnectionCard({
  connection,
  statusConfig,
  refreshing,
  onRefresh,
  onRevoke,
}: {
  connection: TokenConnection;
  statusConfig: { icon: React.ReactNode; label: string; color: string; bg: string };
  refreshing: boolean;
  onRefresh: () => void;
  onRevoke: () => void;
}) {
  return (
    <motion.div
      whileHover={{ scale: 1.005 }}
      className="p-4 rounded-xl border border-guardian-border bg-guardian-card"
    >
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-2xl">{connection.providerIcon}</span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium text-sm">{connection.provider}</span>
              {connection.vaultManaged && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-guardian-teal/10 text-guardian-teal border border-guardian-teal/30 font-mono">
                  VAULT
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              {statusConfig.icon}
              <span className={`text-xs ${statusConfig.color}`}>{statusConfig.label}</span>
              {connection.lastUsed && (
                <span className="text-xs text-guardian-muted">
                  • Last used {new Date(connection.lastUsed).toLocaleTimeString()}
                </span>
              )}
            </div>
            <div className="flex flex-wrap gap-1 mt-1.5">
              {connection.scopes.map((scope) => (
                <span
                  key={scope}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-guardian-bg border border-guardian-border text-guardian-muted font-mono"
                >
                  {scope}
                </span>
              ))}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {connection.status === "connected" && (
            <>
              <button
                onClick={onRefresh}
                disabled={refreshing}
                className="p-2 rounded-lg hover:bg-guardian-bg transition-colors"
                title="Refresh token"
              >
                <RefreshCw
                  className={`w-4 h-4 text-guardian-muted ${refreshing ? "animate-spin" : ""}`}
                />
              </button>
              <button
                onClick={onRevoke}
                disabled={refreshing}
                className="p-2 rounded-lg hover:bg-guardian-red/10 transition-colors"
                title="Revoke access"
              >
                <Unlink className="w-4 h-4 text-guardian-red" />
              </button>
            </>
          )}
          {connection.status === "disconnected" && (
            <button
              onClick={onRefresh}
              disabled={refreshing}
              className="px-3 py-1.5 rounded-lg bg-guardian-teal/20 text-guardian-teal text-xs font-medium hover:bg-guardian-teal/30 transition-colors flex items-center gap-1"
            >
              <ExternalLink className="w-3 h-3" />
              Connect
            </button>
          )}
        </div>
      </div>
    </motion.div>
  );
}
