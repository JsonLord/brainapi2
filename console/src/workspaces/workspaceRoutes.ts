export const WORKSPACE_SECTIONS = new Set([
  "",
  "graph",
  "data",
  "observations",
  "vectors",
  "tasks",
  "ingest",
  "settings",
]);

export function workspacePath(slug: string, section = ""): string {
  const safeSection = WORKSPACE_SECTIONS.has(section) ? section : "";
  return `/w/${encodeURIComponent(slug)}/${safeSection}`.replace(/\/$/, "/");
}

export function workspaceSection(pathname: string): string {
  const match = pathname.match(/^\/w\/[^/]+\/?(.*)$/);
  const candidate = (match?.[1] ?? "").split("/")[0];
  return WORKSPACE_SECTIONS.has(candidate) ? candidate : "";
}

export function switchWorkspacePath(pathname: string, slug: string): string {
  return workspacePath(slug, workspaceSection(pathname));
}

export function legacySection(pathname: string): string {
  const candidate = pathname.replace(/^\/+/, "").split("/")[0];
  return WORKSPACE_SECTIONS.has(candidate) ? candidate : "";
}

export function workspaceCacheKey(name: string, brainId: string): string {
  return `${name}:${brainId}`;
}

export interface WorkspaceIdentity {
  slug: string;
  brain_id: string;
  archived?: boolean;
}

export function resolveRoutedWorkspace<T extends WorkspaceIdentity>(
  workspaces: T[],
  slug: string | undefined,
): T | null {
  if (!slug) return null;
  return workspaces.find((workspace) => workspace.slug === slug) ?? null;
}

export function accessibleWorkspaceTabs<T extends WorkspaceIdentity>(
  workspaces: T[],
  isSystemPat: boolean,
  credentialBrainId?: string,
): T[] {
  const accessible = isSystemPat
    ? workspaces
    : workspaces.filter((item) => item.brain_id === credentialBrainId);
  return accessible.filter((item) => !item.archived);
}

export function scopedRequestBrainId(workspace: WorkspaceIdentity): string {
  return workspace.brain_id;
}
