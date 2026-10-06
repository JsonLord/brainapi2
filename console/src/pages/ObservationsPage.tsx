import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  EmptyState,
  Input,
  ResourceList,
  ResourceListContent,
  ResourceListDescription,
  ResourceListItem,
  ResourceListMetadata,
  ResourceListMetadataItem,
  ResourceListTitle,
  SearchInput,
  Select,
} from "lumen-ui-kit";
import { useWorkspace } from "../workspaces/WorkspaceProvider";
import {
  FilterBar,
  InlineField,
  JsonInspector,
  Workbench,
} from "../components/Workbench";

interface Observation {
  id: string;
  text: string;
  resource_id?: string;
  metadata?: Record<string, unknown>;
  inserted_at?: string;
}

export default function ObservationsPage() {
  const { apiFetch, activeWorkspace } = useWorkspace();
  const [observations, setObservations] = useState<Observation[]>([]);
  const [labels, setLabels] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [selectedLabel, setSelectedLabel] = useState("");
  const [skip, setSkip] = useState(0);
  const [limit] = useState(20);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Observation | null>(null);

  useEffect(() => {
    apiFetch<{ labels?: string[] }>("/retrieve/observations/labels")
      .then((res) => setLabels(res.labels ?? []))
      .catch(() => setLabels([]));
  }, [apiFetch, activeWorkspace?.brain_id]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          limit: String(limit),
          skip: String(skip),
        });
        if (query) params.set("query_text", query);
        if (resourceId) params.set("resource_id", resourceId);
        if (selectedLabel) params.set("labels", selectedLabel);
        const res = await apiFetch<{ observations: Observation[] }>(
          `/retrieve/observations?${params}`,
        );
        if (!cancelled) setObservations(res.observations ?? []);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load observations",
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
  }, [apiFetch, activeWorkspace?.brain_id, query, resourceId, selectedLabel, skip, limit]);

  return (
    <Workbench
      flush
      title="Observations"
      description="Filter and inspect observation records attached to memory resources"
      toolbar={
        <div className="flex flex-col gap-3">
          <FilterBar>
            <SearchInput
              aria-label="Search observations"
              placeholder="Search text…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSkip(0);
              }}
              className="h-9 min-w-[14rem] flex-1"
            />
            <InlineField id="observation-resource-id" label="Resource">
              <Input
                id="observation-resource-id"
                value={resourceId}
                onChange={(e) => {
                  setResourceId(e.target.value);
                  setSkip(0);
                }}
                placeholder="Resource ID"
                className="min-w-[12rem]"
              />
            </InlineField>
            <InlineField id="observation-label" label="Label">
              <Select
                id="observation-label"
                value={selectedLabel}
                onChange={(e) => {
                  setSelectedLabel(e.target.value);
                  setSkip(0);
                }}
              >
                <option value="">All labels</option>
                {labels.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </Select>
            </InlineField>
          </FilterBar>
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
            title="Observation"
            subtitle={selected.id}
            value={selected}
            onClose={() => setSelected(null)}
          />
        ) : undefined
      }
    >
      <div className="flex min-h-full flex-col">
        {loading ? (
          <p className="p-6 text-lumen-muted-foreground">Loading…</p>
        ) : observations.length === 0 ? (
          <div className="p-6">
            <EmptyState title="No observations" />
          </div>
        ) : (
          <ResourceList className="divide-y divide-lumen-border">
            {observations.map((obs) => {
              const isSelected = selected?.id === obs.id;
              return (
                <ResourceListItem
                  key={obs.id}
                  className={
                    isSelected
                      ? "cursor-pointer bg-lumen-muted px-4 py-3"
                      : "cursor-pointer px-4 py-3 hover:bg-lumen-muted/40"
                  }
                  onClick={() => setSelected(obs)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelected(obs);
                    }
                  }}
                  tabIndex={0}
                  aria-selected={isSelected}
                >
                  <ResourceListContent>
                    <ResourceListTitle>{obs.text}</ResourceListTitle>
                    <ResourceListDescription className="font-mono text-xs">
                      {obs.id}
                    </ResourceListDescription>
                    <ResourceListMetadata>
                      {obs.resource_id ? (
                        <ResourceListMetadataItem>
                          resource {obs.resource_id}
                        </ResourceListMetadataItem>
                      ) : null}
                      {obs.inserted_at ? (
                        <ResourceListMetadataItem>
                          {obs.inserted_at}
                        </ResourceListMetadataItem>
                      ) : null}
                    </ResourceListMetadata>
                  </ResourceListContent>
                </ResourceListItem>
              );
            })}
          </ResourceList>
        )}
        <div className="mt-auto flex gap-2 border-t border-lumen-border p-3">
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
            disabled={observations.length < limit}
            onClick={() => setSkip(skip + limit)}
          >
            Next
          </Button>
        </div>
      </div>
    </Workbench>
  );
}
