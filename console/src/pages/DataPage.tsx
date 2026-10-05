import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  SearchInput,
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
  Tabs,
  TabsList,
  TabsTrigger,
} from "lumen-ui-kit";
import { useWorkspace } from "../workspaces/WorkspaceProvider";
import { JsonInspector, InlineField, Workbench } from "../components/Workbench";

type Tab = "chunks" | "structured";

export default function DataPage() {
  const { apiFetch, activeWorkspace } = useWorkspace();
  const [tab, setTab] = useState<Tab>("chunks");
  const [query, setQuery] = useState("");
  const [skip, setSkip] = useState(0);
  const [limit] = useState(20);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [types, setTypes] = useState<string[]>([]);
  const [selectedType, setSelectedType] = useState("");

  useEffect(() => {
    if (tab === "structured") {
      apiFetch<{ types?: string[] }>("/retrieve/structured-data/types")
        .then((res) => setTypes(res.types ?? []))
        .catch(() => setTypes([]));
    }
  }, [apiFetch, activeWorkspace?.brain_id, tab]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        if (tab === "chunks") {
          const params = new URLSearchParams({
            limit: String(limit),
            skip: String(skip),
          });
          if (query) params.set("query_text", query);
          const res = await apiFetch<{
            data: Record<string, unknown>[];
            total: number;
          }>(`/retrieve/text-chunks?${params}`);
          if (!cancelled) {
            setItems(res.data ?? []);
            setTotal(res.total ?? 0);
          }
        } else {
          const params = new URLSearchParams({
            limit: String(limit),
            skip: String(skip),
          });
          if (query) params.set("query_text", query);
          if (selectedType) params.set("types", selectedType);
          const res = await apiFetch<{
            data: Record<string, unknown>[];
            total: number;
          }>(`/retrieve/structured-data?${params}`);
          if (!cancelled) {
            setItems(res.data ?? []);
            setTotal(res.total ?? 0);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load data");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [apiFetch, activeWorkspace?.brain_id, tab, query, skip, limit, selectedType]);

  const start = total === 0 ? 0 : skip + 1;
  const end = Math.min(skip + limit, total);
  const selectedId = selected
    ? String(selected.id ?? selected.resource_id ?? "Record")
    : "";

  return (
    <Workbench
      flush
      title="Data"
      description={`${total.toLocaleString()} records in the active brain memory store`}
      toolbar={
        <div className="flex flex-col gap-3">
          <Tabs
            value={tab}
            onValueChange={(value) => {
              setTab(value as Tab);
              setSkip(0);
              setSelected(null);
            }}
          >
            <TabsList>
              <TabsTrigger value="chunks">Text chunks</TabsTrigger>
              <TabsTrigger value="structured">Structured data</TabsTrigger>
            </TabsList>
          </Tabs>
          <TableToolbar>
            <TableToolbarFilters className="sm:items-center">
              <SearchInput
                aria-label="Search data"
                placeholder="Search records…"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setSkip(0);
                }}
                className="h-9 min-w-[16rem] flex-1"
              />
              {tab === "structured" && (
                <InlineField id="data-type-filter" label="Type">
                  <Select
                    id="data-type-filter"
                    value={selectedType}
                    onChange={(e) => {
                      setSelectedType(e.target.value);
                      setSkip(0);
                    }}
                  >
                    <option value="">All types</option>
                    {types.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </Select>
                </InlineField>
              )}
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
            title="Selected record"
            subtitle={selectedId}
            value={selected}
            onClose={() => setSelected(null)}
          />
        ) : undefined
      }
    >
      <Table containerProps={{ "aria-label": "Data records" }}>
        <TableHeader>
          <TableRow>
            <TableHead>ID</TableHead>
            <TableHead>Preview</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableEmptyState colSpan={2} title="Loading…" />
          ) : items.length === 0 ? (
            <TableEmptyState colSpan={2} title="No results" />
          ) : (
            items.map((item, index) => {
              const id = String(item.id ?? item.resource_id ?? `row-${index}`);
              const preview =
                tab === "chunks"
                  ? String(item.text ?? item.content ?? "").slice(0, 160)
                  : JSON.stringify(item.json_data ?? item).slice(0, 160);
              const isSelected = selected === item;
              return (
                <TableRow
                  key={id}
                  onClick={() => setSelected(item)}
                  aria-selected={isSelected}
                  className={
                    isSelected
                      ? "cursor-pointer bg-lumen-muted"
                      : "cursor-pointer"
                  }
                >
                  <TableCell className="max-w-[180px] truncate font-mono text-xs">
                    {id}
                  </TableCell>
                  <TableCell className="text-lumen-muted-foreground">
                    {preview}
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
