import { useEffect } from "react";
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { getSession } from "./lib/api";
import Layout from "./components/Layout";
import LoginPage from "./pages/LoginPage";
import OverviewPage from "./pages/OverviewPage";
import GraphPage from "./pages/GraphPage";
import DataPage from "./pages/DataPage";
import ObservationsPage from "./pages/ObservationsPage";
import VectorsPage from "./pages/VectorsPage";
import TasksPage from "./pages/TasksPage";
import IngestPage from "./pages/IngestPage";
import WorkspaceSettingsPage from "./pages/WorkspaceSettingsPage";
import { WorkspaceProvider, useWorkspace } from "./workspaces/WorkspaceProvider";
import { legacySection, workspacePath } from "./workspaces/workspaceRoutes";

function RequireAuth({ children }: { children: React.ReactNode }) {
  return getSession() ? <>{children}</> : <Navigate to="/login" replace />;
}

function WorkspaceState() {
  const { activeWorkspace, requestedSlug, loading, error, notFound, workspaces, canManageWorkspaces } = useWorkspace();
  if (loading) return <div className="grid min-h-64 place-items-center">Loading workspaces…</div>;
  if (notFound) return <div className="m-8 rounded border p-8"><h1 className="text-xl font-semibold">Workspace not found</h1><p>The workspace “{requestedSlug}” is unavailable.</p><div className="mt-4 flex gap-2">{workspaces.map((item) => <a key={item.slug} className="underline" href={`/console${workspacePath(item.slug)}`}>{item.display_name}</a>)}</div></div>;
  if (activeWorkspace?.archived) return <div className="m-8 rounded border p-8"><h1 className="text-xl font-semibold">Workspace archived</h1><p>This workspace is read-only and cannot execute brain operations.</p></div>;
  if (error) return <div className="m-8 rounded border p-8" role="alert">{error}</div>;
  if (!activeWorkspace) return <div className="m-8 rounded border p-8"><h1 className="text-xl font-semibold">No workspace available</h1><p>{canManageWorkspaces ? "Create your first workspace with the + action." : "Your credentials do not have an accessible workspace."}</p></div>;
  return <Outlet />;
}

function LegacyRedirect() {
  const { workspaces, loading } = useWorkspace();
  const location = useLocation();
  const navigate = useNavigate();
  const session = getSession();
  useEffect(() => {
    if (loading || !workspaces.length) return;
    const selected = workspaces.find((item) => item.brain_id === session?.brainId) ?? workspaces[0];
    navigate(workspacePath(selected.slug, legacySection(location.pathname)), { replace: true });
  }, [loading, location.pathname, navigate, session?.brainId, workspaces]);
  return <div className="grid min-h-64 place-items-center">Selecting workspace…</div>;
}

function AuthenticatedRoutes() {
  return <RequireAuth><WorkspaceProvider><Outlet /></WorkspaceProvider></RequireAuth>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AuthenticatedRoutes />}>
        <Route path="w/:workspaceSlug" element={<WorkspaceState />}>
          <Route element={<Layout />}>
            <Route index element={<OverviewPage />} />
            <Route path="graph" element={<GraphPage />} />
            <Route path="data" element={<DataPage />} />
            <Route path="observations" element={<ObservationsPage />} />
            <Route path="vectors" element={<VectorsPage />} />
            <Route path="tasks" element={<TasksPage />} />
            <Route path="ingest" element={<IngestPage />} />
            <Route path="settings" element={<WorkspaceSettingsPage />} />
          </Route>
        </Route>
        <Route path="*" element={<LegacyRedirect />} />
      </Route>
    </Routes>
  );
}
