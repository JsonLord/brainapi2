import { useEffect, useState } from "react";
import { Button, Field, FieldLabel, StatusIndicator } from "lumen-ui-kit";
import { PageFrame } from "../components/Workbench";
import { useWorkspace } from "../workspaces/WorkspaceProvider";

export default function WorkspaceSettingsPage() {
  const { activeWorkspace, canManageWorkspaces, updateWorkspace } = useWorkspace();
  const [name, setName] = useState(activeWorkspace?.display_name ?? "");
  const [description, setDescription] = useState(activeWorkspace?.description ?? "");
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => { setName(activeWorkspace?.display_name ?? ""); setDescription(activeWorkspace?.description ?? ""); }, [activeWorkspace]);
  if (!activeWorkspace) return null;
  async function save() {
    setMessage(null);
    try { await updateWorkspace({ display_name: name, description }); setMessage("Workspace settings saved."); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "Unable to save settings"); }
  }
  return <PageFrame className="overflow-auto"><div className="mx-auto flex max-w-3xl flex-col gap-5 p-6">
    <div><h1 className="text-2xl font-semibold">Workspace settings</h1><p className="text-lumen-muted-foreground">Metadata and stable integration endpoints.</p></div>
    <Field><FieldLabel htmlFor="settings-name">Display name</FieldLabel><input id="settings-name" className="rounded border p-2" value={name} disabled={!canManageWorkspaces} onChange={(e) => setName(e.target.value)} /></Field>
    <Field><FieldLabel htmlFor="settings-description">Description</FieldLabel><textarea id="settings-description" className="rounded border p-2" value={description} disabled={!canManageWorkspaces} onChange={(e) => setDescription(e.target.value)} /></Field>
    {[['Slug', activeWorkspace.slug], ['Brain ID', activeWorkspace.brain_id], ['API URL', activeWorkspace.api_base_url], ['Console URL', activeWorkspace.console_url]].map(([label, value]) => <div key={label}><div className="text-sm font-medium">{label}</div><div className="flex gap-2"><code className="min-w-0 flex-1 overflow-x-auto rounded bg-lumen-surface p-2">{value}</code><Button type="button" size="small" variant="secondary" onClick={() => void navigator.clipboard.writeText(value)}>Copy</Button></div></div>)}
    <div><span className="mr-2 text-sm font-medium">Status</span><StatusIndicator status={activeWorkspace.archived ? "warning" : "success"}>{activeWorkspace.archived ? "Archived" : "Active"}</StatusIndicator></div>
    {canManageWorkspaces ? <div className="flex gap-2"><Button onClick={() => void save()}>Save changes</Button><Button variant="secondary" onClick={() => void updateWorkspace({ archived: !activeWorkspace.archived })}>{activeWorkspace.archived ? "Restore" : "Archive"}</Button></div> : null}
    {message ? <p role="status">{message}</p> : null}
  </div></PageFrame>;
}
