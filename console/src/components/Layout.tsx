import { useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  AppShell,
  AppShellMain,
  AppShellSidebar,
  AppShellSidebarContent,
  AppShellSidebarFooter,
  AppShellSidebarHeader,
  Button,
  Field,
  FieldLabel,
  GlobalHeader,
  GlobalHeaderActions,
  GlobalHeaderBrand,
  GlobalHeaderInner,
  Select,
  SideNav,
  SideNavGroup,
  SideNavGroupLabel,
  SideNavItem,
  SideNavLink,
  SideNavList,
  SkipLink,
  StatusIndicator,
} from "lumen-ui-kit";
import { clearSession } from "../lib/auth";
import { setSession } from "../lib/api";
import { useWorkspace } from "../workspaces/WorkspaceProvider";
import { workspacePath, workspaceSection } from "../workspaces/workspaceRoutes";
import CreateWorkspaceDialog from "./CreateWorkspaceDialog";

const groups = [
  { label: "Explore", items: [["", "Overview"], ["graph", "Graph"]] },
  { label: "Memory", items: [["data", "Data"], ["observations", "Observations"], ["vectors", "Vectors"]] },
  { label: "Operations", items: [["tasks", "Tasks"], ["ingest", "Ingest"], ["settings", "Settings"]] },
] as const;

export default function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const {
    workspaces,
    activeWorkspace,
    switchWorkspace,
    canManageWorkspaces,
  } = useWorkspace();
  const [createOpen, setCreateOpen] = useState(false);
  const section = workspaceSection(location.pathname);
  const visible = workspaces.filter((workspace) => !workspace.archived);

  function logout() {
    clearSession();
    setSession(null);
    navigate("/login");
  }

  const navigation = activeWorkspace ? (
    <SideNav expression="compact" aria-label="Console sections">
      <SideNavList>
        {groups.map((group) => (
          <SideNavGroup key={group.label}>
            <SideNavGroupLabel>{group.label}</SideNavGroupLabel>
            <SideNavList>
              {group.items.map(([to, label]) => (
                <SideNavItem key={to}>
                  <SideNavLink
                    href={`/console${workspacePath(activeWorkspace.slug, to)}`}
                    current={section === to}
                    onClick={(event) => {
                      event.preventDefault();
                      navigate(workspacePath(activeWorkspace.slug, to));
                    }}
                  >
                    {label}
                  </SideNavLink>
                </SideNavItem>
              ))}
            </SideNavList>
          </SideNavGroup>
        ))}
      </SideNavList>
    </SideNav>
  ) : null;

  return (
    <div className="flex min-h-screen flex-col bg-lumen-background">
      <SkipLink href="#console-main">Skip to main content</SkipLink>
      <GlobalHeader sticky className="border-b border-lumen-border">
        <GlobalHeaderInner className="max-w-none">
          <GlobalHeaderBrand href="/console/">
            <span className="grid size-8 place-items-center bg-lumen-primary text-xs font-bold text-lumen-on-primary">B</span>
            <span className="font-semibold">BrainAPI</span>
          </GlobalHeaderBrand>
          <div className="mx-4 hidden min-w-0 flex-1 items-center gap-1 overflow-x-auto md:flex" role="tablist" aria-label="Workspaces">
            {visible.map((workspace) => (
              <Button
                key={workspace.slug}
                role="tab"
                aria-selected={workspace.slug === activeWorkspace?.slug}
                variant={workspace.slug === activeWorkspace?.slug ? "primary" : "ghost"}
                size="small"
                className="shrink-0"
                onClick={() => switchWorkspace(workspace.slug)}
                title={workspace.slug}
              >
                {workspace.display_name}
              </Button>
            ))}
            {canManageWorkspaces ? (
              <Button size="small" variant="secondary" className="shrink-0" aria-label="Create workspace" onClick={() => setCreateOpen(true)}>+</Button>
            ) : null}
          </div>
          <div className="mr-2 min-w-0 flex-1 md:hidden">
            {visible.length > 1 ? (
              <Field>
                <FieldLabel htmlFor="mobile-workspace">Workspace</FieldLabel>
                <Select id="mobile-workspace" value={activeWorkspace?.slug ?? ""} onChange={(event) => switchWorkspace(event.target.value)}>
                  {visible.map((workspace) => <option key={workspace.slug} value={workspace.slug}>{workspace.display_name}</option>)}
                </Select>
              </Field>
            ) : <span className="text-sm font-medium">{activeWorkspace?.display_name}</span>}
          </div>
          <GlobalHeaderActions>
            <StatusIndicator status="success" className="hidden sm:inline-flex">Live</StatusIndicator>
            <Button size="small" variant="secondary" onClick={logout}>Log out</Button>
          </GlobalHeaderActions>
        </GlobalHeaderInner>
      </GlobalHeader>
      <AppShell layout="sidebar" className="min-h-0 flex-1 overflow-hidden">
        <AppShellSidebar className="hidden min-h-0 lg:flex">
          <AppShellSidebarHeader>
            <p className="text-xs uppercase text-lumen-muted-foreground">Workspace</p>
            <p className="font-medium">{activeWorkspace?.display_name ?? "None"}</p>
            <p className="font-mono text-xs text-lumen-muted-foreground">{activeWorkspace?.brain_id}</p>
          </AppShellSidebarHeader>
          <AppShellSidebarContent>{navigation}</AppShellSidebarContent>
          <AppShellSidebarFooter className="text-xs text-lumen-muted-foreground">{canManageWorkspaces ? "System credentials" : "Scoped credentials"}</AppShellSidebarFooter>
        </AppShellSidebar>
        <AppShellMain as="main" id="console-main" className="min-h-0 min-w-0 overflow-hidden">
          <Outlet key={activeWorkspace?.brain_id} />
        </AppShellMain>
      </AppShell>
      <CreateWorkspaceDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}
