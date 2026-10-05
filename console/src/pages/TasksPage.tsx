import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Button,
  EmptyState,
  StatusIndicator,
  Table,
  TableBody,
  TableCell,
  TableEmptyState,
  TableHead,
  TableHeader,
  TableRow,
} from "lumen-ui-kit";
import { RestartIcon, Icon } from "lumen-ui-kit/icons";
import { useWorkspace } from "../workspaces/WorkspaceProvider";
import { workspacePath } from "../workspaces/workspaceRoutes";
import { JsonInspector, Workbench } from "../components/Workbench";

interface TaskItem {
  id?: string;
  task_id?: string;
  status: string;
  result?: unknown;
  data?: unknown;
  created_at?: number;
}

const TERMINAL = new Set(["completed", "failed", "error"]);

function statusFor(status: string): "success" | "danger" | "warning" | "neutral" {
  switch (status) {
    case "completed":
      return "success";
    case "failed":
    case "error":
      return "danger";
    case "started":
    case "queued":
      return "warning";
    default:
      return "neutral";
  }
}

export default function TasksPage() {
  const { apiFetch, activeWorkspace } = useWorkspace();
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskItem | null>(null);

  async function loadTasks() {
    try {
      const res = await apiFetch<{ tasks: TaskItem[] }>("/tasks/");
      setTasks(res.tasks ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load tasks");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadTasks();
    const interval = setInterval(() => {
      const hasActive = tasks.some((t) => !TERMINAL.has(t.status));
      if (hasActive || tasks.length === 0) loadTasks();
    }, 5000);
    return () => clearInterval(interval);
  }, [apiFetch, activeWorkspace?.brain_id]);

  async function selectTask(taskId: string) {
    setSelectedId(taskId);
    try {
      const res = await apiFetch<TaskItem>(`/tasks/${taskId}`);
      setDetail(res);
    } catch {
      setDetail(null);
    }
  }

  const activeCount = tasks.filter((t) => !TERMINAL.has(t.status)).length;

  return (
    <Workbench
      flush
      title="Tasks"
      description={
        loading
          ? "Loading pipeline activity…"
          : `${tasks.length} tasks · ${activeCount} active · auto-refresh every 5s`
      }
      actions={
        <Button type="button" variant="secondary" size="small" onClick={loadTasks}>
          <Icon source={RestartIcon} />
          Refresh
        </Button>
      }
      toolbar={
        error ? (
          <Alert variant="danger" title="Failed to load">
            {error}
          </Alert>
        ) : undefined
      }
      inspector={
        selectedId ? (
          <JsonInspector
            title="Task detail"
            subtitle={selectedId}
            value={detail ?? { id: selectedId, status: "loading" }}
            onClose={() => {
              setSelectedId(null);
              setDetail(null);
            }}
          />
        ) : undefined
      }
    >
      {loading ? (
        <p className="p-6 text-lumen-muted-foreground">Loading…</p>
      ) : tasks.length === 0 ? (
        <div className="p-6">
          <EmptyState
            title="No tasks yet"
            description="Ingest some data to create processing tasks."
          >
            <RouterLink
              to={workspacePath(activeWorkspace!.slug, "ingest")}
              className="text-sm font-medium text-lumen-foreground underline underline-offset-2"
            >
              Go to Ingest
            </RouterLink>
          </EmptyState>
        </div>
      ) : (
        <Table containerProps={{ "aria-label": "Ingestion tasks" }}>
          <TableHeader>
            <TableRow>
              <TableHead>Task ID</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.length === 0 ? (
              <TableEmptyState colSpan={3} title="No tasks" />
            ) : (
              tasks.map((task) => {
                const id = task.id ?? task.task_id ?? "unknown";
                const isSelected = selectedId === id;
                return (
                  <TableRow
                    key={id}
                    onClick={() => selectTask(id)}
                    aria-selected={isSelected}
                    className={
                      isSelected
                        ? "cursor-pointer bg-lumen-muted"
                        : "cursor-pointer"
                    }
                  >
                    <TableCell className="max-w-[240px] truncate font-mono text-xs">
                      {id}
                    </TableCell>
                    <TableCell>
                      <StatusIndicator status={statusFor(task.status)}>
                        {task.status}
                      </StatusIndicator>
                    </TableCell>
                    <TableCell className="text-lumen-muted-foreground">
                      {task.created_at
                        ? new Date(task.created_at * 1000).toLocaleString()
                        : "—"}
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      )}
    </Workbench>
  );
}
