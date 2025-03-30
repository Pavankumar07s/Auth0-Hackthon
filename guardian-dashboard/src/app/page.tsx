"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Shield, Activity, Bell, Eye, Lock, Heart, Wifi, CheckCircle } from "lucide-react";
import ConsentPanel from "@/components/ConsentPanel";
import AuditLog from "@/components/AuditLog";
import TokenStatus from "@/components/TokenStatus";
import IncidentFeed from "@/components/IncidentFeed";
import Auth0Features from "@/components/Auth0Features";

type Tab = "overview" | "consent" | "incidents" | "tokens" | "audit" | "auth0";

export default function DashboardPage() {
  const [activeTab, setActiveTab] = useState<Tab>("overview");

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "overview", label: "Overview", icon: <Heart className="w-4 h-4" /> },
    { id: "consent", label: "Consent", icon: <Shield className="w-4 h-4" /> },
    { id: "incidents", label: "Incidents", icon: <Bell className="w-4 h-4" /> },
    { id: "tokens", label: "Tokens", icon: <Lock className="w-4 h-4" /> },
    { id: "audit", label: "Audit Log", icon: <Eye className="w-4 h-4" /> },
    { id: "auth0", label: "Auth0", icon: <Activity className="w-4 h-4" /> },
  ];

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b border-guardian-border bg-guardian-card/50 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-guardian-teal to-guardian-blue flex items-center justify-center">
              <Shield className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-guardian-text">ETMS Guardian Angel</h1>
              <p className="text-xs text-guardian-muted">Caregiver Dashboard</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-guardian-green/10 border border-guardian-green/30">
              <Wifi className="w-3 h-3 text-guardian-green animate-pulse-glow" />
              <span className="text-xs text-guardian-green font-medium">System Active</span>
            </div>
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-guardian-teal/10 border border-guardian-teal/30">
              <CheckCircle className="w-3 h-3 text-guardian-teal" />
              <span className="text-xs text-guardian-teal font-medium">Auth0 Protected</span>
            </div>
          </div>
        </div>
      </header>

      {/* Navigation */}
      <nav className="border-b border-guardian-border bg-guardian-card/30">
        <div className="max-w-7xl mx-auto px-4">
          <div className="flex gap-1 overflow-x-auto py-2">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all whitespace-nowrap
                  ${
                    activeTab === tab.id
                      ? "bg-guardian-teal/20 text-guardian-teal border border-guardian-teal/30"
                      : "text-guardian-muted hover:text-guardian-text hover:bg-guardian-card"
                  }`}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </nav>

      {/* Content */}
      <main className="max-w-7xl mx-auto px-4 py-6">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
          >
            {activeTab === "overview" && <OverviewPanel />}
            {activeTab === "consent" && <ConsentPanel />}
            {activeTab === "incidents" && <IncidentFeed />}
            {activeTab === "tokens" && <TokenStatus />}
            {activeTab === "audit" && <AuditLog />}
            {activeTab === "auth0" && <Auth0Features />}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}

function OverviewPanel() {
  const stats = [
    {
      label: "System Status",
      value: "All Clear",
      color: "text-guardian-green",
      bg: "bg-guardian-green/10",
      icon: <CheckCircle className="w-5 h-5" />,
    },
    {
      label: "Active Agents",
      value: "3 Online",
      color: "text-guardian-teal",
      bg: "bg-guardian-teal/10",
      icon: <Activity className="w-5 h-5" />,
    },
    {
      label: "Consent Items",
      value: "5 Active",
      color: "text-guardian-blue",
      bg: "bg-guardian-blue/10",
      icon: <Shield className="w-5 h-5" />,
    },
    {
      label: "Last Incident",
      value: "2h ago",
      color: "text-guardian-amber",
      bg: "bg-guardian-amber/10",
      icon: <Bell className="w-5 h-5" />,
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-guardian-text mb-1">Welcome back, Caregiver</h2>
        <p className="text-guardian-muted">Your loved one is safe. You are in control.</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((stat) => (
          <motion.div
            key={stat.label}
            whileHover={{ scale: 1.02 }}
            className="p-4 rounded-xl border border-guardian-border bg-guardian-card"
          >
            <div className="flex items-center gap-3 mb-3">
              <div className={`p-2 rounded-lg ${stat.bg}`}>
                <span className={stat.color}>{stat.icon}</span>
              </div>
              <span className="text-sm text-guardian-muted">{stat.label}</span>
            </div>
            <p className={`text-xl font-bold ${stat.color}`}>{stat.value}</p>
          </motion.div>
        ))}
      </div>

      <div className="p-6 rounded-xl border border-guardian-border bg-guardian-card">
        <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Shield className="w-5 h-5 text-guardian-teal" />
          Auth0 Security Summary
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <SecurityItem
            title="Token Vault"
            description="Google Calendar & Slack tokens secured via Auth0 Token Vault. Your credentials are never stored locally."
            status="active"
          />
          <SecurityItem
            title="CIBA Push Authorization"
            description="Critical incidents require your phone approval via Auth0 Guardian push notifications."
            status="active"
          />
          <SecurityItem
            title="FGA Controls"
            description="AI agents can only access authorized data streams. Health records require explicit consent."
            status="active"
          />
        </div>
      </div>
    </div>
  );
}

function SecurityItem({
  title,
  description,
  status,
}: {
  title: string;
  description: string;
  status: "active" | "inactive";
}) {
  return (
    <div className="p-4 rounded-lg bg-guardian-bg/50 border border-guardian-border">
      <div className="flex items-center gap-2 mb-2">
        <div
          className={`w-2 h-2 rounded-full ${
            status === "active" ? "bg-guardian-green animate-pulse-glow" : "bg-guardian-red"
          }`}
        />
        <span className="font-medium text-sm">{title}</span>
      </div>
      <p className="text-xs text-guardian-muted leading-relaxed">{description}</p>
    </div>
  );
}
