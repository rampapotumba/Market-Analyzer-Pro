/** Shared signal status color + label mapping — single source of truth. */

export const SIGNAL_STATUS: Record<string, { color: string; bg: string; label: string }> = {
  created:   { color: "#f0a000", bg: "#f0a00018", label: "CREATED"   },
  tracking:  { color: "#58a6ff", bg: "#58a6ff18", label: "TRACKING"  },
  completed: { color: "#22c55e", bg: "#22c55e18", label: "DONE"      },
  cancelled: { color: "#8b949e", bg: "#8b949e18", label: "CANCELLED" },
};

export function signalStatusColor(status: string): string {
  return SIGNAL_STATUS[status]?.color ?? "#8b949e";
}

export function signalStatusLabel(status: string): string {
  return SIGNAL_STATUS[status]?.label ?? status.toUpperCase();
}
