import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  createWorkspace as createWorkspaceRequest,
  fetchWorkspaces,
  getSession,
  updateWorkspace as updateWorkspaceRequest,
  workspaceApiFetch,
  type CreateWorkspaceInput,
  type UpdateWorkspaceInput,
  type Workspace,
} from "../lib/api";
import { resolveRoutedWorkspace, switchWorkspacePath, workspacePath } from "./workspaceRoutes";

interface WorkspaceContextValue {
  workspaces: Workspace[];
  activeWorkspace: Workspace | null;
  requestedSlug?: string;
  loading: boolean;
  error: string | null;
  notFound: boolean;
  canManageWorkspaces: boolean;
  refreshWorkspaces: () => Promise<Workspace[]>;
  switchWorkspace: (slug: string) => void;
  createWorkspace: (input: CreateWorkspaceInput) => Promise<Workspace>;
  updateWorkspace: (input: UpdateWorkspaceInput) => Promise<Workspace>;
  apiFetch: <T>(path: string, options?: RequestInit) => Promise<T>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const { workspaceSlug } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const session = getSession();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshWorkspaces = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const items = await fetchWorkspaces();
      setWorkspaces(items);
      return items;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load workspaces");
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshWorkspaces();
  }, [refreshWorkspaces]);

  const activeWorkspace = useMemo(
    () => resolveRoutedWorkspace(workspaces, workspaceSlug),
    [workspaceSlug, workspaces],
  );
  const notFound = !loading && Boolean(workspaceSlug) && !activeWorkspace;

  const switchWorkspace = useCallback(
    (slug: string) => navigate(switchWorkspacePath(location.pathname, slug)),
    [location.pathname, navigate],
  );

  const createWorkspace = useCallback(
    async (input: CreateWorkspaceInput) => {
      const created = await createWorkspaceRequest(input);
      await refreshWorkspaces();
      navigate(workspacePath(created.slug));
      return created;
    },
    [navigate, refreshWorkspaces],
  );

  const updateWorkspace = useCallback(
    async (input: UpdateWorkspaceInput) => {
      if (!activeWorkspace) throw new Error("No active workspace");
      const updated = await updateWorkspaceRequest(activeWorkspace.slug, input);
      await refreshWorkspaces();
      return updated;
    },
    [activeWorkspace, refreshWorkspaces],
  );

  const scopedFetch = useCallback(
    <T,>(path: string, options: RequestInit = {}) => {
      if (!activeWorkspace || activeWorkspace.archived) {
        return Promise.reject(new Error("No active workspace"));
      }
      return workspaceApiFetch<T>(activeWorkspace, path, options);
    },
    [activeWorkspace],
  );

  return (
    <WorkspaceContext.Provider
      value={{
        workspaces,
        activeWorkspace,
        requestedSlug: workspaceSlug,
        loading,
        error,
        notFound,
        canManageWorkspaces: Boolean(session?.isSystemPat),
        refreshWorkspaces,
        switchWorkspace,
        createWorkspace,
        updateWorkspace,
        apiFetch: scopedFetch,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return value;
}
