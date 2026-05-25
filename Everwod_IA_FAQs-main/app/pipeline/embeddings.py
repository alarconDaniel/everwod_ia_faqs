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

EMBEDDING_MODEL: Optional[Any] = None
EMBEDDING_MODEL_READY = False
EMBEDDING_BACKEND_READY = "hash"

def load_embedding_model() -> None:
    global EMBEDDING_MODEL, EMBEDDING_MODEL_READY, EMBEDDING_BACKEND_READY
    if EMBEDDING_MODEL_READY:
        return

    if FAQ_EMBEDDING_BACKEND != "hash":
        try:
            from sentence_transformers import SentenceTransformer

            EMBEDDING_MODEL = SentenceTransformer(MODEL_NAME)
            EMBEDDING_BACKEND_READY = MODEL_NAME
        except Exception as exc:
            print(f"No se pudo cargar {MODEL_NAME}. Se usara embedding hash local. Error: {exc}")
            EMBEDDING_MODEL = None
            EMBEDDING_BACKEND_READY = "hash"
    EMBEDDING_MODEL_READY = True

def _as_vector(raw: Any) -> List[float]:
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    return [float(value) for value in raw]

def _normalize_vector(vector: List[float]) -> List[float]:
    norm = sum(value * value for value in vector) ** 0.5
    if norm <= 0:
        return vector
    return [value / norm for value in vector]

def _dot(left: List[float], right: List[float]) -> float:
    return sum(a * b for a, b in zip(left, right))

def _mean_vector(vectors: List[List[float]]) -> List[float]:
    size = len(vectors[0])
    return [sum(vector[index] for vector in vectors) / len(vectors) for index in range(size)]

def _hash_embedding(text: str, dimension: int = FAQ_HASH_EMBEDDING_DIM) -> List[float]:
    vector = [0.0] * dimension
    for token in re.findall(r"\w+", fold_text(text)):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    return _normalize_vector(vector)

def encode_texts(texts: List[str]) -> List[List[float]]:
    load_embedding_model()
    clean_texts = [normalize_text(text) for text in texts]
    if EMBEDDING_MODEL is None:
        return [_hash_embedding(text) for text in clean_texts]

    raw_embeddings = EMBEDDING_MODEL.encode(
        clean_texts,
        convert_to_numpy=False,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [_normalize_vector(_as_vector(embedding)) for embedding in raw_embeddings]

def current_embedding_model_label() -> str:
    return EMBEDDING_BACKEND_READY if EMBEDDING_BACKEND_READY != "hash" else "hash"
