import { useEffect, useState } from "react";
import { apiFetch } from "../lib/api";
export default function PersistenceWarning() {
  const [mode, setMode] = useState<string | null>(null);
  useEffect(() => { void apiFetch<{persistence?: {mode?: string}}>("/health").then((state) => setMode(state.persistence?.mode ?? "unknown")).catch(() => setMode("unknown")); }, []);
  if (mode === null || mode === "durable") return null;
  return <div role="alert" className="border border-amber-500 bg-amber-50 p-3 text-amber-950">
    {mode === "ephemeral" ? "Ephemeral storage: memory can be lost when this container is replaced. This instance is not production durable." : "Storage durability could not be verified. Production readiness is unconfirmed."}
  </div>;
}
