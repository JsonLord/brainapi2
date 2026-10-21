import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Callout,
  PasswordField,
  Stack,
  TextField,
} from "lumen-ui-kit";
import { loadSession, saveSession } from "../lib/auth";
import { connectSession, setSession } from "../lib/api";

export default function LoginPage() {
  const navigate = useNavigate();
  const existing = loadSession();
  const defaultApiBaseUrl = window.location.origin === "http://localhost:5173" || window.location.origin === "http://localhost:4173"
    ? "http://localhost:8000"
    : window.location.origin;

  const [apiBaseUrl, setApiBaseUrl] = useState(
    existing?.apiBaseUrl ?? defaultApiBaseUrl,
  );
  const [pat, setPat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const session = await connectSession(apiBaseUrl, pat);
      setPat("");
      saveSession(session);
      setSession(session);
      navigate("/");
    } catch (err) {
      setSession(null);
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
      <aside className="relative hidden flex-col justify-between border-r border-lumen-border bg-lumen-surface p-10 lg:flex">
        <div>
          <div className="grid size-10 place-items-center bg-lumen-primary text-sm font-bold text-lumen-on-primary">
            B
          </div>
          <h1 className="mt-8 text-3xl font-semibold tracking-tight text-lumen-foreground">
            BrainAPI
            <span className="mt-2 block text-lg font-normal text-lumen-muted-foreground">
              Operations console
            </span>
          </h1>
          <p className="mt-4 max-w-sm text-sm leading-relaxed text-lumen-muted-foreground">
            Connect a local or remote BrainAPI instance with an access token, then
            inspect memory, graph topology, vectors, and ingestion tasks.
          </p>
        </div>
        <dl className="grid gap-4 text-sm">
          <div>
            <dt className="text-lumen-muted-foreground">Typical workflow</dt>
            <dd className="mt-1 text-lumen-foreground">
              Ingest → Tasks → Graph / Data
            </dd>
          </div>
          <div>
            <dt className="text-lumen-muted-foreground">Auth modes</dt>
            <dd className="mt-1 text-lumen-foreground">
              Administrator or workspace token
            </dd>
          </div>
        </dl>
      </aside>

      <main className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-md">
          <div className="mb-8 lg:hidden">
            <div className="grid size-9 place-items-center bg-lumen-primary text-xs font-bold text-lumen-on-primary">
              B
            </div>
            <h1 className="mt-4 text-2xl font-semibold text-lumen-foreground">
              BrainAPI Console
            </h1>
          </div>

          <h2 className="text-xl font-semibold text-lumen-foreground">
            Connect instance
          </h2>
          <p className="mt-1 text-sm text-lumen-muted-foreground">
            Use your API base URL and an access token to enter the workspace.
          </p>

          <form onSubmit={handleSubmit} className="mt-8">
            <Stack gap="md">
              <TextField
                id="login-api-base-url"
                label="API base URL"
                type="url"
                value={apiBaseUrl}
                onChange={(e) => setApiBaseUrl(e.target.value)}
                required
              />
              <PasswordField
                id="login-brainpat"
                label="Access token"
                value={pat}
                onChange={(e) => setPat(e.target.value)}
                placeholder="bpat_… or administrator token"
                required
              />
              <Callout title="Token types">
                <p className="mb-2">
                  <strong>Administrator token</strong> — from{" "}
                  <code>SYSTEM_PAT</code> configured by your deployment operator. Select an available workspace after login.
                </p>
                <p>
                  <strong>Workspace token</strong> — an explicitly created, named credential scoped to one workspace.
                </p>
              </Callout>
              {error && (
                <Alert variant="danger" title="Connection failed">
                  {error}
                </Alert>
              )}
              <Button
                type="submit"
                isFullWidth
                isPending={loading}
                pendingLabel="Connecting…"
              >
                Connect to console
              </Button>
            </Stack>
          </form>
        </div>
      </main>
    </div>
  );
}
