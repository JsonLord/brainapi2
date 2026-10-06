import { useEffect, useState } from "react";
import { Button, Drawer, DrawerContent, DrawerDescription, DrawerTitle, Field, FieldLabel } from "lumen-ui-kit";
import { useWorkspace } from "../workspaces/WorkspaceProvider";

export function suggestWorkspaceSlug(value: string): string {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 63);
}

export default function CreateWorkspaceDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { createWorkspace } = useWorkspace();
  const [displayName, setDisplayName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!slugEdited) setSlug(suggestWorkspaceSlug(displayName));
  }, [displayName, slugEdited]);

  useEffect(() => {
    if (!open) {
      setDisplayName("");
      setSlug("");
      setDescription("");
      setSlugEdited(false);
      setError(null);
    }
  }, [open]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await createWorkspace({ slug, display_name: displayName, description: description || null });
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create workspace");
    } finally { setSaving(false); }
  }

  return (
    <Drawer open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DrawerContent side="right" className="w-[min(28rem,100vw)] p-6">
        <DrawerTitle>Create workspace</DrawerTitle>
        <DrawerDescription>Create an isolated brain workspace and stable API URL.</DrawerDescription>
        <form className="mt-6 flex flex-col gap-4" onSubmit={submit}>
          <Field><FieldLabel htmlFor="workspace-name">Display name</FieldLabel><input id="workspace-name" required className="rounded border p-2" value={displayName} onChange={(e) => setDisplayName(e.target.value)} /></Field>
          <Field><FieldLabel htmlFor="workspace-slug">Slug</FieldLabel><input id="workspace-slug" required className="rounded border p-2 font-mono" value={slug} onChange={(e) => { setSlugEdited(true); setSlug(e.target.value); }} /></Field>
          <Field><FieldLabel htmlFor="workspace-description">Description</FieldLabel><textarea id="workspace-description" className="rounded border p-2" value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
          {error ? <p role="alert" className="text-sm text-red-600">{error}</p> : null}
          <div className="flex justify-end gap-2"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" disabled={saving}>{saving ? "Creating…" : "Create"}</Button></div>
        </form>
      </DrawerContent>
    </Drawer>
  );
}
