"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/signals", label: "Signals" },
  { href: "/simulator", label: "Simulator" },
  { href: "/backtests", label: "Backtests" },
  { href: "/macro", label: "Macro" },
  { href: "/calendar", label: "Calendar" },
  { href: "/accuracy", label: "Accuracy" },
  { href: "/logs", label: "Logs" },
  { href: "/settings", label: "Settings" },
];

export function Navbar() {
  const pathname = usePathname();

  return (
    <nav style={{ borderBottom: '1px solid #30363d', background: '#161b22' }}>
      <div style={{ maxWidth: 1280, margin: '0 auto', padding: '0 16px' }}>
        <div style={{ display: 'flex', height: 52, alignItems: 'center', gap: 24 }}>
          <span style={{ fontWeight: 700, color: '#e6edf3', fontSize: 13, letterSpacing: '-0.3px', fontFamily: 'monospace' }}>
            Market Analyzer Pro
          </span>
          <div style={{ display: 'flex', gap: 2 }}>
            {NAV_ITEMS.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                style={{
                  borderRadius: 6,
                  padding: '6px 12px',
                  fontSize: 13,
                  textDecoration: 'none',
                  fontFamily: 'monospace',
                  transition: 'background 0.15s, color 0.15s',
                  background: pathname === href ? '#30363d' : 'transparent',
                  color: pathname === href ? '#e6edf3' : '#8b949e',
                  fontWeight: pathname === href ? 600 : 400,
                }}
              >
                {label}
              </Link>
            ))}
          </div>
        </div>
      </div>
    </nav>
  );
}
