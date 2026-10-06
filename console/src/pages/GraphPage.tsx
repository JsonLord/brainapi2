import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  ChoiceField,
  ChoiceFieldLabel,
  CodeBlock,
  NumberInput,
  Switch,
  Tag,
  Toolbar,
  ToolbarGroup,
  ToolbarItem,
  ToolbarLabel,
  ToolbarSeparator,
  ToolbarSpacer,
} from "lumen-ui-kit";
import {
  GraphExplorer,
  GraphInspector,
  getGraphTone,
  type GraphFilterState,
  type GraphNode,
  type GraphPerformanceOptions,
  type GraphPropertyValue,
  type GraphRelationship as LumenRelationship,
  type GraphSelection,
} from "lumen-ui-kit/graph";
import { RestartIcon, Icon } from "lumen-ui-kit/icons";
import { useWorkspace } from "../workspaces/WorkspaceProvider";
import {
  filterRelationshipsByEntities,
  filterRelationshipsByLabel,
  filterRelationshipsByQuery,
  mergeGraphData,
  normalizeTriples,
  type GraphEntity,
  type GraphRelationship,
} from "../lib/graphModel";
import { Workbench } from "../components/Workbench";

const LOAD_PRESETS = [100, 250, 500, 1000, 2000] as const;
const MIN_LIMIT = 1;
const MAX_LIMIT = 5000;

function clampLimit(value: number): number {
  if (!Number.isFinite(value)) return 250;
  return Math.min(MAX_LIMIT, Math.max(MIN_LIMIT, Math.round(value)));
}

interface NeighborEntry {
  neighbor: GraphEntity;
  relationship: { name: string; uuid?: string };
}

const EMPTY_FILTERS: GraphFilterState = {
  query: "",
  nodeLabels: [],
  relationshipTypes: [],
};

function toPropertyValue(value: unknown): GraphPropertyValue | undefined {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (Array.isArray(value)) {
    const scalars = value.filter(
      (item): item is string | number | boolean | null =>
        item === null ||
        typeof item === "string" ||
        typeof item === "number" ||
        typeof item === "boolean"
    );
    return scalars;
  }
  return JSON.stringify(value);
}

function toLumenProperties(
  properties?: Record<string, unknown>
): Record<string, GraphPropertyValue> | undefined {
  if (!properties) return undefined;
  const next: Record<string, GraphPropertyValue> = {};
  for (const [key, value] of Object.entries(properties)) {
    const mapped = toPropertyValue(value);
    if (mapped !== undefined) next[key] = mapped;
  }
  return next;
}

function entityToNode(entity: GraphEntity): GraphNode {
  const labels = entity.labels?.length ? entity.labels : ["Entity"];
  return {
    id: entity.uuid,
    label: entity.name,
    labels,
    description: entity.description,
    properties: toLumenProperties(entity.properties),
    tone: getGraphTone(labels[0] ?? "Entity"),
  };
}

function relationshipToLumen(
  rel: GraphRelationship,
  index: number
): LumenRelationship {
  const type = rel.predicate.name || "RELATED";
  return {
    id:
      rel.predicate.uuid ??
      `${rel.subject.uuid}-${type}-${rel.object.uuid}-${index}`,
    source: rel.subject.uuid,
    target: rel.object.uuid,
    type,
    label: type,
    directed: true,
  };
}

function mergeEntities(
  current: GraphEntity[],
  incoming: GraphEntity[]
): GraphEntity[] {
  const map = new Map(current.map((e) => [e.uuid, e]));
  for (const e of incoming) map.set(e.uuid, e);
  return [...map.values()];
}

function mergeRelationships(
  current: GraphRelationship[],
  incoming: GraphRelationship[]
): GraphRelationship[] {
  const map = new Map(
    current.map((r) => [
      `${r.subject.uuid}-${r.object.uuid}-${r.predicate.name}`,
      r,
    ])
  );
  for (const r of incoming) {
    map.set(`${r.subject.uuid}-${r.object.uuid}-${r.predicate.name}`, r);
  }
  return [...map.values()];
}

function applyGraphFilters(
  rels: GraphRelationship[],
  entities: GraphEntity[],
  label: string,
  query: string
): GraphRelationship[] {
  const hasFilters = !!(label || query);
  if (!hasFilters) return rels;

  if (label && query) {
    return filterRelationshipsByQuery(
      filterRelationshipsByLabel(rels, label),
      query
    );
  }
  if (label) {
    return filterRelationshipsByLabel(rels, label);
  }
  if (entities.length > 0) {
    return filterRelationshipsByEntities(rels, entities);
  }
  return filterRelationshipsByQuery(rels, query);
}

function asEntityOrFallback(
  value: GraphEntity | null | undefined,
  fallback: GraphEntity | null
): GraphEntity {
  if (value?.uuid) return value;
  if (fallback) return fallback;
  throw new Error("missing entity");
}

export default function GraphPage() {
  const { apiFetch, activeWorkspace } = useWorkspace();
  const [entities, setEntities] = useState<GraphEntity[]>([]);
  const [relationships, setRelationships] = useState<GraphRelationship[]>([]);
  const [limit, setLimit] = useState(250);
  const [draftLimit, setDraftLimit] = useState(250);
  const [loading, setLoading] = useState(true);
  const [expanding, setExpanding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [physicsEnabled, setPhysicsEnabled] = useState(true);
  const [groupingEnabled, setGroupingEnabled] = useState(true);
  const [filterState, setFilterState] =
    useState<GraphFilterState>(EMPTY_FILTERS);
  const [selection, setSelection] = useState<GraphSelection>(null);
  const activeBrainId = activeWorkspace?.brain_id ?? "";

  const performance = useMemo<GraphPerformanceOptions | false | undefined>(
    () => (groupingEnabled ? undefined : { clustering: false }),
    [groupingEnabled]
  );

  const loadGraph = useCallback(async (nextLimit: number) => {
    const applied = clampLimit(nextLimit);
    setLoading(true);
    setError(null);
    setSelection(null);

    try {
      const relParams = new URLSearchParams({
        limit: String(applied),
        skip: "0",
      });

      const [relRes] = await Promise.all([
        apiFetch<{ relationships: unknown[]; total?: number }>(
          `/retrieve/relationships?${relParams}`
        ),
      ]);

      const normalizedRels = normalizeTriples(relRes.relationships ?? []);
      const filteredRels = applyGraphFilters(normalizedRels, [], "", "");
      const merged = mergeGraphData([], filteredRels);

      if (merged.entities.length === 0) {
        const fallback = await apiFetch<{ entities: GraphEntity[] }>(
          `/retrieve/entities?limit=${applied}&skip=0`
        );
        setEntities(
          mergeGraphData(fallback.entities ?? [], normalizedRels).entities
        );
        setRelationships(normalizedRels);
      } else {
        setEntities(merged.entities);
        setRelationships(merged.relationships);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load graph");
    } finally {
      setLoading(false);
    }
  }, [apiFetch, activeWorkspace?.brain_id]);

  useEffect(() => {
    loadGraph(limit);
  }, [loadGraph]);

  function applyLoadLimit(next?: number) {
    const applied = clampLimit(next ?? draftLimit);
    setDraftLimit(applied);
    setLimit(applied);
    void loadGraph(applied);
  }

  const nodes = useMemo(() => entities.map(entityToNode), [entities]);
  const lumenRelationships = useMemo(
    () => relationships.map(relationshipToLumen),
    [relationships]
  );

  const entityById = useMemo(() => {
    const map = new Map(entities.map((entity) => [entity.uuid, entity]));
    return map;
  }, [entities]);

  async function expandNode(center: GraphEntity) {
    setExpanding(true);
    try {
      const res = await apiFetch<{
        main_node: GraphEntity;
        neighbors?: NeighborEntry[];
      }>(
        `/retrieve/entities/neighbors?uuid=${encodeURIComponent(center.uuid)}&limit=40`
      );

      const centerNode = asEntityOrFallback(res.main_node, center);
      const neighbors = res.neighbors ?? [];
      const newEntities: GraphEntity[] = [centerNode];
      const newRels: GraphRelationship[] = [];

      for (const entry of neighbors) {
        const neighbor = asEntityOrFallback(entry.neighbor, null);
        if (!neighbor) continue;
        newEntities.push(neighbor);
        newRels.push({
          subject: centerNode,
          object: neighbor,
          predicate: {
            name: entry.relationship?.name?.trim() || "RELATED",
            uuid: entry.relationship?.uuid,
          },
        });
      }

      setEntities((prev) => mergeEntities(prev, newEntities));
      setRelationships((prev) => mergeRelationships(prev, newRels));
      setSelection({ kind: "node", id: centerNode.uuid });
    } catch {
      /* ignore */
    } finally {
      setExpanding(false);
    }
  }

  return (
    <Workbench
      flush
      title="Graph"
      description={`Brain ${activeBrainId} · ${nodes.length} nodes · ${lumenRelationships.length} relationships · limit ${limit}`}
      toolbar={
        <div className="flex flex-col gap-3">
          <Toolbar
            aria-label="Graph load controls"
            density="compact"
            className="w-full items-center px-4"
          >
            <ToolbarLabel id="graph-load-limit-label">Load limit</ToolbarLabel>
            <ToolbarGroup
              aria-labelledby="graph-load-limit-label"
              variant="segmented"
            >
              {LOAD_PRESETS.map((preset) => (
                <ToolbarItem key={preset}>
                  <Button
                    type="button"
                    size="small"
                    variant="secondary"
                    aria-pressed={draftLimit === preset}
                    onClick={() => setDraftLimit(preset)}
                  >
                    {preset.toLocaleString()}
                  </Button>
                </ToolbarItem>
              ))}
            </ToolbarGroup>
            <ToolbarSeparator />
            <ToolbarGroup
              aria-label="Custom load limit"
              className="items-center"
            >
              <ToolbarItem className="flex items-center">
                <NumberInput
                  id="graph-custom-limit"
                  aria-label="Custom load limit"
                  min={MIN_LIMIT}
                  max={MAX_LIMIT}
                  step={50}
                  value={draftLimit}
                  onChange={(e) =>
                    setDraftLimit(clampLimit(Number(e.target.value)))
                  }
                  className="h-9 w-24"
                />
              </ToolbarItem>
            </ToolbarGroup>
            <ToolbarSeparator />
            <ToolbarGroup
              aria-label="Display options"
              className="items-center"
            >
              <ToolbarItem className="flex items-center">
                <ChoiceField className="items-center gap-2">
                  <Switch
                    id="graph-node-grouping"
                    checked={groupingEnabled}
                    onChange={(e) => setGroupingEnabled(e.target.checked)}
                  />
                  <ChoiceFieldLabel htmlFor="graph-node-grouping">
                    Group nodes
                  </ChoiceFieldLabel>
                </ChoiceField>
              </ToolbarItem>
            </ToolbarGroup>
            <ToolbarSpacer />
            <ToolbarGroup
              aria-label="Load actions"
              className="items-center"
            >
              <ToolbarItem>
                <Button
                  type="button"
                  size="small"
                  variant="secondary"
                  onClick={() => applyLoadLimit()}
                  disabled={loading}
                >
                  Apply
                </Button>
              </ToolbarItem>
              <ToolbarItem>
                <Button
                  type="button"
                  size="small"
                  onClick={() => applyLoadLimit(limit)}
                  isPending={loading}
                  pendingLabel="Loading…"
                >
                  <Icon source={RestartIcon} />
                  Reload
                </Button>
              </ToolbarItem>
            </ToolbarGroup>
          </Toolbar>
          {error ? (
            <Alert
              variant="danger"
              title="Failed to load graph"
            >
              {error}
            </Alert>
          ) : null}
        </div>
      }
    >
      {loading ? (
        <div className="flex min-h-0 flex-1 items-center justify-center text-lumen-muted-foreground">
          Loading graph…
        </div>
      ) : nodes.length === 0 ? (
        <div className="flex min-h-0 flex-1 items-start p-6">
          <Alert title="No nodes in this brain">
            Active brain: {activeBrainId}
            {activeBrainId === "default"
              ? ". System PAT defaults to default — switch to your ingest brain in the header."
              : ". If you expected data here, confirm ingest used this same brain id."}
          </Alert>
        </div>
      ) : (
        <GraphExplorer
          ariaLabel="Knowledge graph explorer"
          className="graph-explorer--fill min-h-0 flex-1"
          nodes={nodes}
          relationships={lumenRelationships}
          filterState={filterState}
          onFilterStateChange={setFilterState}
          selection={selection}
          onSelectionChange={setSelection}
          physicsEnabled={physicsEnabled}
          onPhysicsEnabledChange={setPhysicsEnabled}
          performance={performance}
          renderInspector={(context) => {
            if (context.selection.kind !== "node") {
              return (
                <GraphInspector
                  {...context}
                  onClose={context.close}
                />
              );
            }
            const entity = entityById.get(context.selection.id);
            const node = context.nodes.find(
              (n) => n.id === context.selection.id
            );
            return (
              <aside className="flex h-full flex-col gap-3 overflow-auto border-l border-lumen-border p-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h2 className="text-lg font-semibold text-lumen-foreground">
                      {node?.label ?? entity?.name ?? context.selection.id}
                    </h2>
                    <p className="mt-0.5 font-mono text-[10px] text-lumen-muted-foreground">
                      {context.selection.id}
                    </p>
                  </div>
                  <Button
                    type="button"
                    size="small"
                    variant="ghost"
                    onClick={context.close}
                  >
                    Close
                  </Button>
                </div>
                {node?.labels?.length ? (
                  <div className="flex flex-wrap gap-1">
                    {node.labels.map((label) => (
                      <Tag key={label}>{label}</Tag>
                    ))}
                  </div>
                ) : null}
                {node?.description ? (
                  <p className="text-sm text-lumen-muted-foreground">
                    {node.description}
                  </p>
                ) : null}
                {entity ? (
                  <Button
                    type="button"
                    variant="secondary"
                    isFullWidth
                    isPending={expanding}
                    pendingLabel="Expanding…"
                    onClick={() => expandNode(entity)}
                  >
                    Expand neighbors
                  </Button>
                ) : null}
                <div className="text-xs font-medium uppercase tracking-wide text-lumen-muted-foreground">
                  Properties
                </div>
                <CodeBlock>
                  {JSON.stringify(node?.properties ?? {}, null, 2)}
                </CodeBlock>
              </aside>
            );
          }}
        />
      )}
    </Workbench>
  );
}
