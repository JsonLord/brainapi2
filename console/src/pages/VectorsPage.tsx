import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  ChoiceField,
  ChoiceFieldLabel,
  Select,
  Table,
  TableBody,
  TableCell,
  TableEmptyState,
  TableHead,
  TableHeader,
  TablePagination,
  TableRow,
  TableToolbar,
  TableToolbarContent,
  TableToolbarFilters,
} from "lumen-ui-kit";
import { useWorkspace } from "../workspaces/WorkspaceProvider";
import { JsonInspector, InlineField, Workbench } from "../components/Workbench";

interface VectorItem {
  id: string;
  embeddings?: number[] | null;
  metadata: Record<string, unknown>;
}

interface VectorStore {
  name: string;
  dimension: number;
}

export default function VectorsPage() {
  const { apiFetch, activeWorkspace } = useWorkspace();
  const [stores, setStores] = useState<VectorStore[]>([]);
  const [store, setStore] = useState("");
  const [vectors, setVectors] = useState<VectorItem[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [limit] = useState(20);
  const [includeEmbeddings, setIncludeEmbeddings] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<VectorItem | null>(null);

  useEffect(() => {
    apiFetch<{ stores: VectorStore[] }>("/retrieve/vectors/stores")
      .then((res) => {
        setStores(res.stores ?? []);
        if (res.stores?.[0]) setStore(res.stores[0].name);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load stores"),
      );
  }, [apiFetch, activeWorkspace?.brain_id]);

  useEffect(() => {
    if (!store) return;
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          limit: String(limit),
          skip: String(skip),
          include_embeddings: String(includeEmbeddings),
        });
        const res = await apiFetch<{
          vectors: VectorItem[];
          total: number;
        }>(`/retrieve/vectors/${store}?${params}`);
        if (!cancelled) {
          setVectors(res.vectors ?? []);
          setTotal(res.total ?? 0);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load vectors",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [apiFetch, activeWorkspace?.brain_id, store, skip, limit, includeEmbeddings]);

  const start = total === 0 ? 0 : skip + 1;
  const end = Math.min(skip + limit, total);

  return (
    <Workbench
      flush
      title="Vectors"
      description={
        store
          ? `Store ${store} · ${total.toLocaleString()} vectors`
          : "Select a vector store"
      }
      toolbar={
        <div className="flex flex-col gap-3">
          <TableToolbar>
            <TableToolbarFilters className="sm:items-center">
              <InlineField id="vector-store" label="Store">
                <Select
                  id="vector-store"
                  value={store}
                  onChange={(e) => {
                    setStore(e.target.value);
                    setSkip(0);
                    setSelected(null);
                  }}
                >
                  {stores.map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.name} (dim {s.dimension})
                    </option>
                  ))}
                </Select>
              </InlineField>
              <ChoiceField className="min-h-9 items-center py-0">
                <Checkbox
                  id="include-embeddings"
                  checked={includeEmbeddings}
                  onChange={(e) => setIncludeEmbeddings(e.target.checked)}
                />
                <ChoiceFieldLabel htmlFor="include-embeddings">
                  Include embeddings
                </ChoiceFieldLabel>
              </ChoiceField>
            </TableToolbarFilters>
            <TableToolbarContent />
          </TableToolbar>
          {error && (
            <Alert variant="danger" title="Failed to load">
              {error}
            </Alert>
          )}
        </div>
      }
      inspector={
        selected ? (
          <JsonInspector
            title="Vector detail"
            subtitle={selected.id}
            value={{
              ...selected,
              embeddings: selected.embeddings
                ? [
                    ...selected.embeddings.slice(0, 16),
                    ...(selected.embeddings.length > 16 ? ["…"] : []),
                  ]
                : null,
            }}
            onClose={() => setSelected(null)}
          />
        ) : undefined
      }
    >
      <Table containerProps={{ "aria-label": "Vectors" }}>
        <TableHeader>
          <TableRow>
            <TableHead>ID</TableHead>
            <TableHead>Metadata</TableHead>
            <TableHead>Dim</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableEmptyState colSpan={3} title="Loading…" />
          ) : vectors.length === 0 ? (
            <TableEmptyState colSpan={3} title="No vectors" />
          ) : (
            vectors.map((v) => {
              const isSelected = selected?.id === v.id;
              return (
                <TableRow
                  key={v.id}
                  onClick={() => setSelected(v)}
                  aria-selected={isSelected}
                  className={
                    isSelected
                      ? "cursor-pointer bg-lumen-muted"
                      : "cursor-pointer"
                  }
                >
                  <TableCell className="max-w-[180px] truncate font-mono text-xs">
                    {v.id}
                  </TableCell>
                  <TableCell className="max-w-md truncate text-lumen-muted-foreground">
                    {JSON.stringify(v.metadata).slice(0, 100)}
                  </TableCell>
                  <TableCell className="text-lumen-muted-foreground">
                    {v.embeddings?.length ?? "—"}
                  </TableCell>
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>
      <TablePagination start={start} end={end} total={total}>
        <Button
          type="button"
          size="small"
          variant="secondary"
          disabled={skip === 0}
          onClick={() => setSkip(Math.max(0, skip - limit))}
        >
          Prev
        </Button>
        <Button
          type="button"
          size="small"
          variant="secondary"
          disabled={skip + limit >= total}
          onClick={() => setSkip(skip + limit)}
        >
          Next
        </Button>
      </TablePagination>
    </Workbench>
  );
}
