import { useEffect, useState } from "react";
import { apiFetch } from "../lib/api";
interface TokenMetadata { id: string; label: string; token_prefix: string; permissions: string[]; created_at: string; last_used_at: string | null; expires_at: string | null; revoked_at: string | null; }
export default function AccessTokens({ brainId }: { brainId: string }) {
  const [tokens, setTokens] = useState<TokenMetadata[]>([]);
  const [label, setLabel] = useState("");
  const [write, setWrite] = useState(false);
  const [issued, setIssued] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function refresh() {
    const result = await apiFetch<{ tokens: TokenMetadata[] }>(`/system/access-tokens?brain_id=${encodeURIComponent(brainId)}`);
    setTokens(result.tokens);
  }
  useEffect(() => { setTokens([]); setIssued(null); void refresh().catch((e: Error) => setError(e.message)); }, [brainId]);
  async function create(event: React.FormEvent) {
    event.preventDefault(); setError(null); setIssued(null);
    try {
      const result = await apiFetch<TokenMetadata & { token: string }>("/system/access-tokens", {
        method: "POST", body: JSON.stringify({ brain_id: brainId, label, permissions: write ? ["memory:read", "memory:write"] : ["memory:read"] }),
      });
      setIssued(result.token); setLabel(""); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to create token"); }
  }
  async function revoke(id: string) {
    try { await apiFetch(`/system/access-tokens/${encodeURIComponent(id)}`, { method: "DELETE" }); setIssued(null); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to revoke token"); }
  }
  return <section className="flex flex-col gap-3" aria-label="Access tokens">
    <h2 className="text-xl font-semibold">Access tokens</h2>
    <form onSubmit={(e) => void create(e)} className="flex flex-wrap gap-3">
      <label>Label <input required maxLength={100} value={label} onChange={(e) => setLabel(e.target.value)} className="rounded border p-2" /></label>
      <label><input type="checkbox" checked={write} onChange={(e) => setWrite(e.target.checked)} /> Allow writes</label>
      <button className="rounded border p-2" type="submit">Create token</button>
    </form>
    {issued ? <div role="status"><p>This token is shown once. Copy it now.</p><code className="break-all">{issued}</code><button onClick={() => void navigator.clipboard.writeText(issued)}>Copy token</button><button onClick={() => setIssued(null)}>Dismiss</button></div> : null}
    {error ? <p role="alert">{error}</p> : null}
    <div className="overflow-x-auto"><table><thead><tr>{["Label", "Prefix", "Permissions", "Created", "Last used", "Expiry", "Status", "Action"].map((h) => <th className="p-2 text-left" key={h}>{h}</th>)}</tr></thead><tbody>
      {tokens.map((token) => <tr key={token.id}><td>{token.label}</td><td>{token.token_prefix}</td><td>{token.permissions.join(", ")}</td><td>{token.created_at}</td><td>{token.last_used_at ?? "Never"}</td><td>{token.expires_at ?? "None"}</td><td>{token.revoked_at ? "Revoked" : token.expires_at && Date.parse(token.expires_at) <= Date.now() ? "Expired" : "Active"}</td><td><button disabled={Boolean(token.revoked_at)} onClick={() => void revoke(token.id)}>Revoke</button></td></tr>)}
    </tbody></table></div>
  </section>;
}
