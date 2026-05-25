from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple, cast

from app.core.common import (
    DATA_DIR,
    FAQ_SCHEMA,
    fold_text,
    get_bool_env,
    get_db_connection,
    get_float_env,
    get_int_env,
    load_json,
    load_json_lines,
    normalize_question,
    normalize_text,
    save_json,
)
from app.core.config import (
    CONVERSATIONS_PATH,
    FAQ_ALIGNMENT_PARTIAL_SIMILARITY,
    FAQ_ALIGNMENT_STRONG_SIMILARITY,
    FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS,
    FAQ_CANDIDATE_HARVEST_MODE,
    FAQ_CLEANING_ENABLED,
    FAQ_CLUSTER_ALGORITHM,
    FAQ_CLUSTER_EPS,
    FAQ_DUPLICATE_THRESHOLD,
    FAQ_EMBEDDING_BACKEND,
    FAQ_ENABLE_HIERARCHICAL_FALLBACK,
    FAQ_HASH_EMBEDDING_DIM,
    FAQ_HDBSCAN_MIN_CLUSTER_SIZE,
    FAQ_HDBSCAN_MIN_SAMPLES,
    FAQ_HIGH_CONFIDENCE_THRESHOLD,
    FAQ_LLM_DEVICE,
    FAQ_LLM_ENABLED,
    FAQ_LLM_MODEL,
    FAQ_LLM_THINKING_DISABLED,
    FAQ_LLM_TORCH_DTYPE,
    FAQ_MAX_CANONICAL_ANSWER_WORDS,
    FAQ_MAX_CANONICAL_QUESTION_WORDS,
    FAQ_MIN_CLUSTER_COHESION,
    FAQ_MIN_CLUSTER_SIZE,
    FAQ_MIN_CLUSTER_SUPPORT,
    FAQ_MIN_GENERATION_CONFIDENCE,
    FAQ_MIN_QUESTION_EVIDENCE,
    FAQ_REJECT_CLUSTER_COHESION,
    FAQ_REJECT_CLUSTER_SUPPORT,
    FAQ_SKIP_EXISTING,
    FAQ_SKIP_REJECTED,
    FAQ_STRIP_EMOJIS,
    FAQ_STRIP_URLS,
    FAQ_SUPPORTED_LANGUAGES,
    FAQ_TWO_EXAMPLE_MIN_COHESION,
    MODEL_NAME,
    SUGGESTIONS_PATH,
)

from app.pipeline.embeddings import _dot

def _fallback_dbscan(embeddings: List[List[float]]) -> List[int]:
    labels = [-1] * len(embeddings)
    cluster_id = 0
    visited = set()
    min_similarity = 1.0 - FAQ_CLUSTER_EPS
    min_samples = 2 if FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS else FAQ_MIN_CLUSTER_SIZE

    for index, embedding in enumerate(embeddings):
        if index in visited:
            continue
        neighbors = [idx for idx, other in enumerate(embeddings) if _dot(embedding, other) >= min_similarity]
        visited.add(index)
        if len(neighbors) < min_samples:
            continue
        queue = list(neighbors)
        labels[index] = cluster_id
        while queue:
            neighbor = queue.pop()
            if neighbor not in visited:
                visited.add(neighbor)
                expanded = [
                    idx
                    for idx, other in enumerate(embeddings)
                    if _dot(embeddings[neighbor], other) >= min_similarity
                ]
                if len(expanded) >= min_samples:
                    queue.extend(idx for idx in expanded if idx not in queue)
            if labels[neighbor] == -1:
                labels[neighbor] = cluster_id
        cluster_id += 1

    return labels

def _hierarchical_cluster_embeddings(embeddings: List[List[float]]) -> List[int]:
    if not FAQ_ENABLE_HIERARCHICAL_FALLBACK or len(embeddings) < 2:
        return [-1] * len(embeddings)

    try:
        import numpy as np
        from sklearn.cluster import AgglomerativeClustering

        x_matrix = np.asarray(embeddings, dtype=float)

        model = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=FAQ_CLUSTER_EPS,
            metric="cosine",
            linkage="average",
        )

        raw_labels = [int(label) for label in model.fit_predict(x_matrix)]
        counts = Counter(raw_labels)
        min_size = 2 if FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS else FAQ_MIN_CLUSTER_SIZE

        remap: Dict[int, int] = {}
        next_label = 0
        labels: List[int] = []

        for label in raw_labels:
            if counts[label] < min_size:
                labels.append(-1)
                continue

            if label not in remap:
                remap[label] = next_label
                next_label += 1

            labels.append(remap[label])

        return labels

    except Exception:
        return [-1] * len(embeddings)

def _hdbscan_cluster_embeddings(embeddings: List[List[float]]) -> List[int]:
    """
    HDBSCAN opcional usando sklearn.cluster.HDBSCAN cuando está disponible.

    Se importa dinámicamente para evitar falsos positivos de Pylance en entornos
    donde sklearn sí lo trae en runtime, pero los stubs no lo exponen bien.
    """
    if len(embeddings) < 2:
        return [-1] * len(embeddings)

    try:
        import numpy as np
        import sklearn.cluster as sklearn_cluster

        hdbscan_cls = getattr(sklearn_cluster, "HDBSCAN", None)
        if hdbscan_cls is None:
            print("HDBSCAN no está disponible en esta versión de scikit-learn. Se usará DBSCAN.")
            return [-1] * len(embeddings)

        x_matrix = np.asarray(embeddings, dtype=float)

        model = hdbscan_cls(
            min_cluster_size=FAQ_HDBSCAN_MIN_CLUSTER_SIZE,
            min_samples=FAQ_HDBSCAN_MIN_SAMPLES,
            metric="euclidean",
        )

        raw_labels = [int(label) for label in model.fit_predict(x_matrix)]

        counts = Counter(label for label in raw_labels if label != -1)
        if not counts:
            return [-1] * len(embeddings)

        remap: Dict[int, int] = {}
        next_label = 0
        labels: List[int] = []

        for label in raw_labels:
            if label == -1:
                labels.append(-1)
                continue

            if label not in remap:
                remap[label] = next_label
                next_label += 1

            labels.append(remap[label])

        return labels

    except Exception as exc:
        print(f"HDBSCAN no disponible o falló durante clustering. Se usará DBSCAN. Error: {exc}")
        return [-1] * len(embeddings)

def cluster_embeddings(embeddings: List[List[float]]) -> List[int]:
    """
    Clustering adaptativo.

    DBSCAN con eps fijo puede crear clusters gigantes por efecto cadena.
    Eso mezcla intenciones y luego descarta todo el cluster, perdiendo FAQs útiles.
    Esta versión reintenta con eps más estricto cuando detecta un cluster gigante.
    """
    if not embeddings:
        return []
    
    if FAQ_CLUSTER_ALGORITHM in {"hdbscan", "auto"}:
        hdbscan_labels = _hdbscan_cluster_embeddings(embeddings)
        hdbscan_cluster_count = len({label for label in hdbscan_labels if label != -1})

    if FAQ_CLUSTER_ALGORITHM == "hdbscan":
        return hdbscan_labels

    if hdbscan_cluster_count >= 2:
        print(f"HDBSCAN activo: clusters detectados={hdbscan_cluster_count}")
        return hdbscan_labels    

    def run_dbscan(eps_value: float) -> List[int]:
        try:
            import numpy as np
            from sklearn.cluster import DBSCAN

            x_matrix = np.asarray(embeddings, dtype=float)
            min_samples = 2 if FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS else FAQ_MIN_CLUSTER_SIZE

            model = DBSCAN(
                eps=eps_value,
                min_samples=min_samples,
                metric="cosine",
            )

            return [int(label) for label in model.fit_predict(x_matrix)]

        except Exception:
            return _fallback_dbscan(embeddings)

    def cluster_stats(labels: List[int]) -> Tuple[int, int, float]:
        counts = Counter(label for label in labels if label != -1)
        if not counts:
            return 0, 0, 0.0
        largest = max(counts.values())
        cluster_count = len(counts)
        largest_ratio = largest / max(1, len(labels))
        return cluster_count, largest, largest_ratio

    labels = run_dbscan(FAQ_CLUSTER_EPS)
    cluster_count, _largest, largest_ratio = cluster_stats(labels)

    if cluster_count == 0 and FAQ_ENABLE_HIERARCHICAL_FALLBACK:
        fallback_labels = _hierarchical_cluster_embeddings(embeddings)
        if any(label != -1 for label in fallback_labels):
            return fallback_labels

    if largest_ratio >= 0.35 and len(embeddings) >= 100:
        candidate_eps_values = [
            max(0.18, FAQ_CLUSTER_EPS - 0.04),
            max(0.18, FAQ_CLUSTER_EPS - 0.06),
            max(0.18, FAQ_CLUSTER_EPS - 0.08),
        ]

        best_labels = labels
        best_cluster_count = cluster_count
        best_largest_ratio = largest_ratio

        for eps_value in candidate_eps_values:
            candidate_labels = run_dbscan(eps_value)
            candidate_cluster_count, _candidate_largest, candidate_largest_ratio = cluster_stats(candidate_labels)

            if candidate_cluster_count == 0:
                continue

            improves_giant_cluster = candidate_largest_ratio < best_largest_ratio
            keeps_useful_clusters = candidate_cluster_count >= max(2, best_cluster_count)

            if improves_giant_cluster and keeps_useful_clusters:
                best_labels = candidate_labels
                best_cluster_count = candidate_cluster_count
                best_largest_ratio = candidate_largest_ratio

        if best_labels != labels:
            print(
                "DBSCAN adaptativo: cluster gigante refinado. "
                f"clusters {cluster_count}->{best_cluster_count}, "
                f"largest_ratio {round(largest_ratio, 4)}->{round(best_largest_ratio, 4)}"
            )
            return best_labels

    return labels

def compute_silhouette(embeddings: List[List[float]], labels: List[int]) -> Optional[float]:
    clean_pairs = [
        (embedding, label)
        for embedding, label in zip(embeddings, labels)
        if label != -1
    ]
    clean_labels = [label for _, label in clean_pairs]

    if len(set(clean_labels)) <= 1 or len(clean_pairs) <= len(set(clean_labels)):
        return None

    try:
        import numpy as np
        from sklearn.metrics import silhouette_score

        x_matrix = np.asarray([embedding for embedding, _label in clean_pairs], dtype=float)

        return round(
            float(
                silhouette_score(
                    x_matrix,
                    clean_labels,
                    metric="cosine",
                )
            ),
            4,
        )

    except Exception:
        return None
