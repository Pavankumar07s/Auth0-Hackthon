import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ETMS Guardian Angel — Caregiver Dashboard",
  description:
    "Your loved one is safe. You are in control. Auth0-powered elderly safety monitoring with full caregiver consent and AI agent transparency.",
  keywords: ["elderly safety", "caregiver dashboard", "Auth0", "AI agents", "ETMS"],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="antialiased">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body className="min-h-screen bg-guardian-bg text-guardian-text selection:bg-guardian-teal/30">
        {children}
      </body>
    </html>
  );
}
