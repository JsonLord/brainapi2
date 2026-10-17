"""
File: /entity_sibilings.py
Created Date: Monday January 12th 2026
Author: Christian Nonis <alch.infoemail@gmail.com>
-----
Last Modified: Monday February 2nd 2026 10:04:06 pm
Modified By: Christian Nonis <alch.infoemail@gmail.com>
-----
"""

from typing import Dict, List, Literal, Optional, Tuple
import numpy as np

from src.adapters.interfaces.graph import PredicateWithFlowKey
from src.constants.kg import EntitySynergy, Node, Predicate
from src.services.kg_agent.main import (
    embeddings_adapter,
    graph_adapter,
    vector_store_adapter,
)
from src.utils.vector_search import VectorSearchFacade
from src.utils.similarity.numbers import wmean, wsim
from src.utils.similarity.vectors import cosine_similarity

DIRECT_MULTIPLIER = 1.30

REMOTE_MULTIPLIER = 0.70

FACTORS_INCREMENTAL_WEIGHT = 0.3

NODE_SIM_DESC_INCREMENTAL_WEIGHT = 0.5

DEFAULT_TOP_K = 50

vector_search = VectorSearchFacade(vector_store_adapter)


def _predicate_currently_valid(predicate: Predicate) -> bool:
    props = getattr(predicate, "properties", None) or {}
    if props.get("invalid_at"):
        return False
    if getattr(predicate, "deprecated", False):
        return False
    return True


def _normalize_polarity(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip().lower()
    if v in ("positive", "negative", "neutral"):
        return v
    return None


def polarity_matches(
    target_polarity: Optional[str],
    candidate_polarity: Optional[str],
    mode: Literal["same", "opposite"],
) -> bool:
    """
    same: matching polarities (None/neutral treated as compatible with each other).
    opposite: positive↔negative only; neutral/None excluded from opposite matches.
    """
    tp = _normalize_polarity(target_polarity)
    cp = _normalize_polarity(candidate_polarity)
    if mode == "same":
        if tp in (None, "neutral") and cp in (None, "neutral"):
            return True
        return tp == cp
    if tp in (None, "neutral") or cp in (None, "neutral"):
        return False
    return {tp, cp} == {"positive", "negative"}


class EntitySinergyRetriever:
    def __init__(self, brain_id: str = "default"):
        """
        Initialize the retriever with the identifier of the brain (knowledge graph) to operate against.

        Parameters:
            brain_id (str): Identifier of the brain/knowledge graph to use for adapter calls. Defaults to "default".
        """
        self.brain_id = brain_id

    def retrieve_sibilings(
        self,
        target: str,
        polarity: Literal["same", "opposite"],
        do: bool = False,
        pa: bool = False,
        ppa: bool = False,
        top_k: int = DEFAULT_TOP_K,
        labels: Optional[List[str]] = None,
    ) -> Tuple[Optional[Node], List[EntitySynergy], List[Node], List[Node]]:
        """
        Find related entities ("siblings") for a given target text by locating the target node and assembling EntitySynergy connections from its neighbors and embedding-based similarity.

        Parameters:
            target: The text used to locate the target entity in the node store.
            polarity: "same" keeps candidates with matching node polarity; "opposite"
                keeps positive↔negative pairs only.
            do: If True, only direct synergies are returned.
            pa: If True, potential anchors are returned.
            ppa: If True, anchors (seed nodes) are returned.
            top_k: Maximum synergies to return (default 50).
            labels: If set, only return candidates whose labels intersect this set.

        Returns:
            (target_node, connections, anchors, potential_anchors)
        """

        label_filter = None
        _embedding_cache: Dict[Tuple[str, str], Optional[np.ndarray]] = {}

        def _get_first_embedding(ids: List[str], store: str):
            if not ids:
                return None
            key = (ids[0], store)
            if key not in _embedding_cache:
                _embeddings = vector_store_adapter.get_by_ids(
                    ids,
                    store=store,
                    brain_id=self.brain_id,
                )
                _embedding_cache[key] = (
                    _embeddings[0].embeddings if _embeddings else None
                )
            return _embedding_cache[key]

        def _association_score(tn_score, rel_score, multiplier):
            return (
                (np.mean([tn_score, rel_score], axis=0) * (multiplier))
                if tn_score and rel_score
                else 0.0
            )

        def _upsert_connection(seed_node: Node, neighbor_node: Node, score: float):
            if neighbor_node.uuid == target_node.uuid:
                return
            if label_filter is not None:
                if not set(l.upper() for l in neighbor_node.labels).intersection(
                    label_filter
                ):
                    return
            if not polarity_matches(
                target_node.polarity, neighbor_node.polarity, polarity
            ):
                return
            if neighbor_node.uuid not in positivep_connections:
                positivep_connections[neighbor_node.uuid] = EntitySynergy(
                    node=neighbor_node,
                    connected_by=[seed_node],
                    association_score=score,
                )
            else:
                if seed_node.uuid in (
                    n.uuid
                    for n in positivep_connections[neighbor_node.uuid].connected_by
                ):
                    return
                pas = positivep_connections[neighbor_node.uuid].association_score
                nas = score
                n_association_score = wmean([pas, nas], FACTORS_INCREMENTAL_WEIGHT)
                positivep_connections[neighbor_node.uuid].connected_by.append(seed_node)
                positivep_connections[neighbor_node.uuid].association_score = (
                    n_association_score
                )
            seen_set.add(seed_node.uuid)

        def _collect_seed_nodes_for_neighbor(neighbor):
            seed_nodes_for_neighbor = []
            target_embeddings = []
            if not _predicate_currently_valid(neighbor[0]):
                return None, None
            neighbor_embeddings = _get_first_embedding(
                [neighbor[0].properties.get("v_id")],
                store="relationships",
            )
            if neighbor_embeddings is None:
                return None, None
            if "EVENT" in neighbor[1].labels:
                flow_key = neighbor[0].flow_key or neighbor[0].properties.get(
                    "flow_key"
                )
                if not flow_key:
                    return None, None
                next_rels = graph_adapter.get_nexts_by_flow_key(
                    [
                        PredicateWithFlowKey(
                            predicate_uuid=neighbor[0].uuid,
                            flow_key=flow_key,
                        )
                    ],
                    brain_id=self.brain_id,
                )
                rel_v_ids = []
                next_tuples = []
                for t in next_rels[neighbor[0].uuid]:
                    if target_node.uuid in [t[2].uuid, t[0].uuid]:
                        continue
                    v_id = t[1].properties.get("v_id")
                    if not v_id:
                        continue
                    rel_v_ids.append(v_id)
                    next_tuples.append((v_id, t))
                rel_embeddings_map = {}
                if rel_v_ids:
                    rel_vectors = vector_store_adapter.get_by_ids(
                        rel_v_ids,
                        store="relationships",
                        brain_id=self.brain_id,
                    )
                    for vid, v in zip(rel_v_ids, rel_vectors):
                        if v.embeddings is not None:
                            _embedding_cache[(vid, "relationships")] = v.embeddings
                            rel_embeddings_map[vid] = v.embeddings
                collected_embeddings = []
                for v_id, t in next_tuples:
                    target_embedding_for_edge = rel_embeddings_map.get(v_id)
                    if not target_embedding_for_edge:
                        continue
                    collected_embeddings.append(target_embedding_for_edge)
                    seed_nodes_for_neighbor.append(t[2])
                if collected_embeddings:
                    target_embeddings = np.mean(
                        [neighbor_embeddings] + collected_embeddings,
                        axis=0,
                    )
                else:
                    target_embeddings = neighbor_embeddings
            else:
                seed_nodes_for_neighbor = [neighbor[1]]
                target_embeddings = neighbor_embeddings
            return seed_nodes_for_neighbor, target_embeddings

        def _process_seed(seed_node: Node, direct: bool, target_embeddings):
            MULTIPLIER = DIRECT_MULTIPLIER if direct else REMOTE_MULTIPLIER

            if seed_node.uuid in seen_set:
                return

            _neighbors_direct = graph_adapter.get_neighbors(
                [seed_node.uuid],
                of_types=target_node.labels,
                brain_id=self.brain_id,
            )

            _neighbors_event_edges = graph_adapter.get_neighbors(
                [seed_node.uuid],
                of_types=["EVENT"],
                brain_id=self.brain_id,
            )
            edges_map = {
                edge[0].uuid: edge[0]
                for edge in _neighbors_event_edges[seed_node.uuid]
                if _predicate_currently_valid(edge[0])
            }
            _neighbors_event = graph_adapter.get_nexts_by_flow_key(
                [
                    PredicateWithFlowKey(
                        predicate_uuid=edge_uuid,
                        flow_key=edges_map[edge_uuid].flow_key,
                    )
                    for edge_uuid in edges_map.keys()
                ],
                brain_id=self.brain_id,
            )

            rel_ids_to_fetch = []
            node_ids_to_fetch = []
            for edge_uuid, n in _neighbors_event.items():
                for e in n:
                    if e[2].uuid != target_node.uuid and set(e[2].labels).intersection(
                        target_node.labels
                    ):
                        edge_vid = edges_map[edge_uuid].properties.get("v_id")
                        if edge_vid:
                            rel_ids_to_fetch.append(edge_vid)
                            en_vid = e[1].properties.get("v_id")
                            if en_vid:
                                rel_ids_to_fetch.append(en_vid)
                            neighbor_node_vid = e[2].properties.get("v_id")
                            if neighbor_node_vid:
                                node_ids_to_fetch.append(neighbor_node_vid)
            for dn in _neighbors_direct[seed_node.uuid]:
                if not _predicate_currently_valid(dn[0]):
                    continue
                if dn[1].uuid != target_node.uuid:
                    n_vid = dn[0].properties.get("v_id")
                    if n_vid:
                        rel_ids_to_fetch.append(n_vid)
            unique_rel_ids = list(dict.fromkeys(rel_ids_to_fetch))
            if unique_rel_ids:
                rel_vectors = vector_store_adapter.get_by_ids(
                    unique_rel_ids,
                    store="relationships",
                    brain_id=self.brain_id,
                )
                for vid, v in zip(unique_rel_ids, rel_vectors):
                    if v.embeddings is not None:
                        _embedding_cache[(vid, "relationships")] = v.embeddings
            unique_node_ids = list(dict.fromkeys(node_ids_to_fetch))
            if unique_node_ids:
                node_vectors = vector_store_adapter.get_by_ids(
                    unique_node_ids,
                    store="nodes",
                    brain_id=self.brain_id,
                )
                for vid, v in zip(unique_node_ids, node_vectors):
                    if v.embeddings is not None:
                        _embedding_cache[(vid, "nodes")] = v.embeddings

            _neighbors = []
            for edge_uuid, n in _neighbors_event.items():
                for e in n:
                    if e[2].uuid != target_node.uuid:
                        tn_score = None
                        rel_score = None
                        if not set(e[2].labels).intersection(target_node.labels):
                            continue
                        edge_vid = edges_map[edge_uuid].properties.get("v_id")
                        if edge_vid:
                            edge_embedding = _get_first_embedding(
                                [edge_vid],
                                store="relationships",
                            )
                            if edge_embedding is not None:
                                en_vid = e[1].properties.get("v_id")
                                if en_vid:
                                    en_embedding = _get_first_embedding(
                                        [en_vid],
                                        store="relationships",
                                    )
                                    if en_embedding is not None:
                                        found_embeddings = np.mean(
                                            [
                                                en_embedding,
                                                edge_embedding,
                                            ],
                                            axis=0,
                                        )
                                        neighbor_node_vid = e[2].properties.get("v_id")
                                        tn_score = None
                                        if neighbor_node_vid:
                                            neighbor_node_embedding = (
                                                _get_first_embedding(
                                                    [neighbor_node_vid],
                                                    store="nodes",
                                                )
                                            )
                                            if neighbor_node_embedding is not None:
                                                tn_score = wsim(
                                                    cosine_similarity(
                                                        neighbor_node_embedding,
                                                        target_embedding,
                                                    ),
                                                    NODE_SIM_DESC_INCREMENTAL_WEIGHT,
                                                )
                                        rel_score = cosine_similarity(
                                            found_embeddings,
                                            target_embeddings,
                                        )
                        _neighbors.append(
                            (
                                _association_score(
                                    tn_score,
                                    rel_score,
                                    MULTIPLIER,
                                ),
                                e[2],
                            )
                        )

            for dn in _neighbors_direct[seed_node.uuid]:
                if not _predicate_currently_valid(dn[0]):
                    continue
                if dn[1].uuid != target_node.uuid:
                    n_vid = dn[0].properties.get("v_id")
                    tn_score = None
                    rel_score = None
                    if n_vid:
                        n_embedding = _get_first_embedding(
                            [n_vid],
                            store="relationships",
                        )
                        if n_embedding is not None:
                            tn_score = cosine_similarity(
                                n_embedding,
                                target_embedding,
                            )
                            rel_vid = dn[0].properties.get("v_id")
                            if rel_vid:
                                rel_embedding = _get_first_embedding(
                                    [rel_vid],
                                    store="relationships",
                                )
                                if rel_embedding is not None:
                                    rel_score = cosine_similarity(
                                        rel_embedding,
                                        target_embedding,
                                    )
                    _neighbors.append(
                        (
                            _association_score(
                                tn_score,
                                rel_score,
                                MULTIPLIER,
                            ),
                            dn[1],
                        )
                    )

            if not _neighbors:
                return

            for neighbor in _neighbors:
                _upsert_connection(seed_node, neighbor[1], neighbor[0])

        target_embedding = embeddings_adapter.embed_query(target)
        target_node_vs = vector_search.search_nodes(
            target_embedding.embeddings,
            brain_id=self.brain_id,
        )
        if not target_node_vs:
            return None, [], [], []
        target_node_id = target_node_vs[0].metadata.get("uuid")
        target_node = graph_adapter.get_by_uuid(target_node_id, brain_id=self.brain_id)
        if not target_node:
            return None, [], [], []

        _neighbors = graph_adapter.get_neighbors(
            [target_node_id], brain_id=self.brain_id
        )
        neighbors = [
            n for n in _neighbors[target_node_id] if _predicate_currently_valid(n[0])
        ]

        positivep_connections: Dict[str, EntitySynergy] = {}
        seen_set = set([target_node.uuid])
        label_filter = (
            {l.upper() for l in labels} if labels else None
        )

        all_seed_nodes: List[Node] = []
        all_similar_seeds: List[Node] = []

        target_vid = target_node.properties.get("v_id")
        target_embedding = None
        if target_vid:
            target_embedding = _get_first_embedding([target_vid], store="nodes")

        for neighbor in neighbors:
            seed_nodes_for_neighbor, target_embeddings = (
                _collect_seed_nodes_for_neighbor(neighbor)
            )
            if seed_nodes_for_neighbor is None:
                continue

            all_seed_nodes.extend(seed_nodes_for_neighbor)

            for seed_node in seed_nodes_for_neighbor:
                similar_seeds = []
                if ppa or not do:
                    seed_vid = seed_node.properties.get("v_id")
                    if seed_vid:
                        seed_embedding = _get_first_embedding(
                            [seed_vid],
                            store="nodes",
                        )
                        if seed_embedding is None:
                            continue
                        similar_seed_vs = vector_search.search_nodes(
                            seed_embedding,
                            brain_id=self.brain_id,
                        )

                        similar_seeds = graph_adapter.get_by_uuids(
                            [vs.metadata.get("uuid") for vs in similar_seed_vs],
                            brain_id=self.brain_id,
                        )

                _process_seed(
                    seed_node, direct=True, target_embeddings=target_embeddings
                )

                if pa or not do:
                    all_similar_seeds.extend(similar_seeds)

                if not do:
                    for similar_seed in similar_seeds:
                        _process_seed(
                            similar_seed,
                            direct=False,
                            target_embeddings=target_embeddings,
                        )

        unique_seeds = {n.uuid: n for n in all_seed_nodes}
        ranked = sorted(
            positivep_connections.values(),
            key=lambda x: x.association_score,
            reverse=True,
        )
        limit = max(0, int(top_k)) if top_k is not None else DEFAULT_TOP_K
        return (
            target_node,
            ranked[:limit],
            list(unique_seeds.values()),
            all_similar_seeds,
        )
