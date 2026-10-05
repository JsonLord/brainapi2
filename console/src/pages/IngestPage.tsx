import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Button,
  Field,
  FieldLabel,
  SectionBand,
  SectionBandContent,
  SectionBandDescription,
  SectionBandEyebrow,
  SectionBandHeader,
  SectionBandTitle,
  SectionStack,
  Stack,
  Textarea,
} from "lumen-ui-kit";
import { PageFrame } from "../components/Workbench";
import { useWorkspace } from "../workspaces/WorkspaceProvider";
import { workspacePath } from "../workspaces/workspaceRoutes";

export default function IngestPage() {
  const { apiFetch, activeWorkspace } = useWorkspace();
  const [text, setText] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [ingestResult, setIngestResult] = useState<string | null>(null);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);


  async function handleIngest(e: React.FormEvent) {
    e.preventDefault();
    setIngesting(true);
    setIngestError(null);
    setIngestResult(null);
    setTaskId(null);
    try {
      const res = await apiFetch<{ message: string; task_id: string }>(
        "/ingest/",
        {
          method: "POST",
          body: JSON.stringify({
            data: { data_type: "text", text_data: text },
          }),
        },
      );
      setIngestResult(res.message);
      setTaskId(res.task_id);
      setText("");
    } catch (err) {
      setIngestError(err instanceof Error ? err.message : "Ingest failed");
    } finally {
      setIngesting(false);
    }
  }

  return (
    <PageFrame className="overflow-auto">
      <SectionStack className="max-w-4xl border border-lumen-border">
        <SectionBand tone="accent">
          <SectionBandHeader>
            <SectionBandEyebrow>Pipeline</SectionBandEyebrow>
            <SectionBandTitle>Ingest</SectionBandTitle>
            <SectionBandDescription>
              Submitting into brain{" "}
              <span className="font-mono text-lumen-foreground">
                {activeWorkspace?.brain_id}
              </span>
              . Completed jobs appear under Tasks.
            </SectionBandDescription>
          </SectionBandHeader>
        </SectionBand>

        <SectionBand>
          <SectionBandHeader>
            <SectionBandEyebrow>Content</SectionBandEyebrow>
            <SectionBandTitle>Text ingest</SectionBandTitle>
            <SectionBandDescription>
              Free-form text is queued for extraction and storage.
            </SectionBandDescription>
          </SectionBandHeader>
          <SectionBandContent>
            <form onSubmit={handleIngest}>
              <Stack gap="md">
                <Field>
                  <FieldLabel htmlFor="ingest-text">Text to ingest</FieldLabel>
                  <Textarea
                    id="ingest-text"
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    rows={8}
                    required
                    placeholder="Emily organized the AI Ethics Meetup in London on March 8, 2024."
                  />
                </Field>
                {ingestError && (
                  <Alert variant="danger" title="Ingest failed">
                    {ingestError}
                  </Alert>
                )}
                {ingestResult && (
                  <Alert variant="success" title="Submitted">
                    {ingestResult}
                    {taskId && (
                      <>
                        {" "}
                        —{" "}
                        <RouterLink
                          to={workspacePath(activeWorkspace!.slug, "tasks")}
                          className="underline underline-offset-2"
                        >
                          View task {taskId}
                        </RouterLink>
                      </>
                    )}
                  </Alert>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="submit"
                    disabled={!text.trim()}
                    isPending={ingesting}
                    pendingLabel="Submitting…"
                  >
                    Ingest text
                  </Button>
                  <RouterLink
                    to={workspacePath(activeWorkspace!.slug, "tasks")}
                    className="inline-flex h-11 items-center border border-lumen-control-border bg-lumen-action-secondary px-4 text-sm font-medium text-lumen-on-action-secondary"
                  >
                    Open tasks
                  </RouterLink>
                </div>
              </Stack>
            </form>
          </SectionBandContent>
        </SectionBand>

      </SectionStack>
    </PageFrame>
  );
}
