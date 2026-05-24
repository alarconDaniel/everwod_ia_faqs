import hashlib
import json
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple, cast

from fastapi import FastAPI, HTTPException
from psycopg2.extras import Json, RealDictCursor

from faq_common import (
    DATA_DIR,
    FAQ_SCHEMA,
    configure_cors,
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
from faq_models import IngestRequest, SuggestionResponse, SuggestionSummary, WorkspaceResponse
from ingest_service import fetch_conversation_records, fetch_conversation_records_with_metrics


app = FastAPI(title="Everwod FAQ Suggestion Service")
configure_cors(app)

MODEL_NAME = os.getenv("FAQ_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
FAQ_EMBEDDING_BACKEND = os.getenv("FAQ_EMBEDDING_BACKEND", "sentence-transformers")
FAQ_LLM_MODEL = os.getenv("FAQ_LLM_MODEL", "Qwen/Qwen3-1.7B")
FAQ_LLM_ENABLED = get_bool_env("FAQ_LLM_ENABLED", False)
FAQ_LLM_DEVICE = os.getenv("FAQ_LLM_DEVICE", "cpu").strip().lower()
FAQ_LLM_TORCH_DTYPE = os.getenv("FAQ_LLM_TORCH_DTYPE", "auto").strip().lower()
FAQ_CLUSTER_EPS = get_float_env("FAQ_CLUSTER_EPS", 0.34)
FAQ_CLEANING_ENABLED = get_bool_env("FAQ_CLEANING_ENABLED", True)
FAQ_STRIP_EMOJIS = get_bool_env("FAQ_STRIP_EMOJIS", True)
FAQ_STRIP_URLS = get_bool_env("FAQ_STRIP_URLS", True)
FAQ_SUPPORTED_LANGUAGES = {
    language.strip().lower()
    for language in os.getenv("FAQ_SUPPORTED_LANGUAGES", "es,en").split(",")
    if language.strip()
}

FAQ_CLUSTER_ALGORITHM = os.getenv("FAQ_CLUSTER_ALGORITHM", "auto").strip().lower()
FAQ_HDBSCAN_MIN_CLUSTER_SIZE = get_int_env("FAQ_HDBSCAN_MIN_CLUSTER_SIZE", 3)
FAQ_HDBSCAN_MIN_SAMPLES = get_int_env("FAQ_HDBSCAN_MIN_SAMPLES", 2)
FAQ_SKIP_REJECTED = get_bool_env("FAQ_SKIP_REJECTED", True)
FAQ_MIN_CLUSTER_SIZE = get_int_env("FAQ_MIN_CLUSTER_SIZE", 3)
FAQ_SKIP_EXISTING = get_bool_env("FAQ_SKIP_EXISTING", True)
FAQ_DUPLICATE_THRESHOLD = get_float_env("FAQ_DUPLICATE_THRESHOLD", 0.78)
FAQ_MIN_CLUSTER_SUPPORT = get_float_env("FAQ_MIN_CLUSTER_SUPPORT", 0.58)
FAQ_MIN_CLUSTER_COHESION = get_float_env("FAQ_MIN_CLUSTER_COHESION", 0.60)
FAQ_MIN_GENERATION_CONFIDENCE = get_float_env("FAQ_MIN_GENERATION_CONFIDENCE", 0.20)
FAQ_HASH_EMBEDDING_DIM = get_int_env("FAQ_HASH_EMBEDDING_DIM", 96)
FAQ_CANDIDATE_HARVEST_MODE = os.getenv("FAQ_CANDIDATE_HARVEST_MODE", "broad").strip().lower()
FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS = get_bool_env("FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS", True)
FAQ_TWO_EXAMPLE_MIN_COHESION = get_float_env("FAQ_TWO_EXAMPLE_MIN_COHESION", 0.80)
FAQ_ENABLE_HIERARCHICAL_FALLBACK = get_bool_env("FAQ_ENABLE_HIERARCHICAL_FALLBACK", True)
FAQ_HIGH_CONFIDENCE_THRESHOLD = 0.72
FAQ_REJECT_CLUSTER_SUPPORT = max(0.35, FAQ_MIN_CLUSTER_SUPPORT - 0.12)
FAQ_REJECT_CLUSTER_COHESION = max(0.35, FAQ_MIN_CLUSTER_COHESION - 0.12)
FAQ_MIN_QUESTION_EVIDENCE = 2
FAQ_MAX_CANONICAL_QUESTION_WORDS = 18
FAQ_MAX_CANONICAL_ANSWER_WORDS = 55
FAQ_LLM_THINKING_DISABLED = "qwen3" in FAQ_LLM_MODEL.lower()
FAQ_ALIGNMENT_STRONG_SIMILARITY = get_float_env("FAQ_ALIGNMENT_STRONG_SIMILARITY", 0.56)
FAQ_ALIGNMENT_PARTIAL_SIMILARITY = get_float_env("FAQ_ALIGNMENT_PARTIAL_SIMILARITY", 0.42)

SUGGESTIONS_PATH = DATA_DIR / "faq_suggestions.json"
CONVERSATIONS_PATH = DATA_DIR / "conversations.jsonl"

EMBEDDING_MODEL: Optional[Any] = None
ANSWER_GENERATOR: Optional[Any] = None
MODELS_READY = False
EMBEDDING_MODEL_READY = False
ANSWER_GENERATOR_READY = False
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


def _resolve_llm_device(torch_module: Any) -> str:
    """
    Decide dónde cargar el LLM.

    Valores soportados por .env:
    - FAQ_LLM_DEVICE=cpu
    - FAQ_LLM_DEVICE=auto
    - FAQ_LLM_DEVICE=cuda

    Nota:
    En PyTorch con ROCm, AMD puede aparecer como 'cuda' si la instalación ROCm está bien montada.
    """
    requested = FAQ_LLM_DEVICE

    if requested == "cpu":
        return "cpu"

    if requested in {"cuda", "gpu"}:
        if torch_module.cuda.is_available():
            return "cuda"
        print("FAQ_LLM_DEVICE=cuda/gpu solicitado, pero torch.cuda.is_available() es False. Se usará CPU.")
        return "cpu"

    if requested == "auto":
        if torch_module.cuda.is_available():
            return "cuda"
        return "cpu"

    print(f"FAQ_LLM_DEVICE={requested!r} no reconocido. Se usará CPU.")
    return "cpu"


def _resolve_torch_dtype(torch_module: Any, device: str) -> Any:
    """
    Dtype configurable.

    Recomendado:
    - CPU: float32 o auto
    - GPU: float16 o auto

    En CPU, float16 puede ser más lento o problemático.
    """
    dtype = FAQ_LLM_TORCH_DTYPE

    if dtype == "float16":
        return torch_module.float16

    if dtype == "bfloat16":
        return torch_module.bfloat16

    if dtype == "float32":
        return torch_module.float32

    if dtype == "auto":
        if device == "cuda":
            return torch_module.float16
        return torch_module.float32

    print(f"FAQ_LLM_TORCH_DTYPE={dtype!r} no reconocido. Se usará auto.")
    if device == "cuda":
        return torch_module.float16
    return torch_module.float32


def load_answer_generator() -> None:
    global ANSWER_GENERATOR, ANSWER_GENERATOR_READY

    if ANSWER_GENERATOR_READY or ANSWER_GENERATOR is not None:
        return

    print(f"LLM model active: {FAQ_LLM_MODEL if FAQ_LLM_ENABLED else 'disabled'}")
    print(f"LLM device requested: {FAQ_LLM_DEVICE}")
    print(f"LLM dtype requested: {FAQ_LLM_TORCH_DTYPE}")
    print(f"Thinking disabled: {FAQ_LLM_THINKING_DISABLED}")

    if FAQ_LLM_ENABLED:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

            resolved_device = _resolve_llm_device(torch)
            resolved_dtype = _resolve_torch_dtype(torch, resolved_device)

            print(f"LLM device resolved: {resolved_device}")
            print(f"torch.cuda.is_available(): {torch.cuda.is_available()}")

            if torch.cuda.is_available():
                try:
                    print(f"torch.cuda device count: {torch.cuda.device_count()}")
                    print(f"torch.cuda device 0: {torch.cuda.get_device_name(0)}")
                except Exception as gpu_info_exc:
                    print(f"No se pudo leer info de GPU desde torch.cuda: {gpu_info_exc}")

            tokenizer = AutoTokenizer.from_pretrained(FAQ_LLM_MODEL)

            model_kwargs: Dict[str, Any] = {
                "torch_dtype": resolved_dtype,
            }

            # En GPU/ROCm compatible, device_map='auto' permite que Transformers/Accelerate
            # ubique el modelo en el acelerador disponible.
            if resolved_device == "cuda":
                model_kwargs["device_map"] = "auto"

            model = AutoModelForCausalLM.from_pretrained(
                FAQ_LLM_MODEL,
                **model_kwargs,
            )

            model = cast(Any, model)

            if resolved_device == "cpu":
                model = model.to("cpu")

            if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
                tokenizer.pad_token = tokenizer.eos_token

            pipeline_kwargs: Dict[str, Any] = {
                "task": "text-generation",
                "model": model,
                "tokenizer": tokenizer,
            }

            # Si usamos device_map='auto', NO pasar device al pipeline.
            # Si es CPU, usar device=-1.
            if resolved_device == "cpu":
                pipeline_kwargs["device"] = -1

            ANSWER_GENERATOR = pipeline(**pipeline_kwargs)

        except Exception as exc:
            print(f"No se pudo cargar {FAQ_LLM_MODEL}. Se usara fallback historico. Error: {exc}")
            ANSWER_GENERATOR = None

    ANSWER_GENERATOR_READY = True

def load_models() -> None:
    global MODELS_READY
    if MODELS_READY:
        return
    load_embedding_model()
    load_answer_generator()
    MODELS_READY = True


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

    No se vuelve obligatorio porque en algunos entornos puede variar la versión de sklearn.
    Si falla, devuelve todo como ruido y el pipeline cae a DBSCAN.
    """
    if len(embeddings) < 2:
        return [-1] * len(embeddings)

    try:
        import numpy as np
        from sklearn.cluster import HDBSCAN

        x_matrix = np.asarray(embeddings, dtype=float)

        model = HDBSCAN(
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
        print(f"HDBSCAN no disponible o fallo durante clustering. Se usara DBSCAN. Error: {exc}")
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


def parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]",
    flags=re.UNICODE,
)

TIME_PATTERN = re.compile(
    r"\b(?:[01]?\d|2[0-3])(?:[:.]\d{2})?\s*(?:a\.?\s*m\.?|p\.?\s*m\.?|am|pm)\b|\b(?:[01]?\d|2[0-3])[:.]\d{2}\b",
    flags=re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
PLACEHOLDER_PATTERN = re.compile(r"\[(?:telefono|tel[eé]fono|correo|email|persona|personas)\]", flags=re.IGNORECASE)
RAW_URL_PATTERN = re.compile(r"https?://|www\.", flags=re.IGNORECASE)

URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", flags=re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_PATTERN = re.compile(r"\b(?:\+?\d[\s().-]?){7,}\b")

CHAT_NOISE_PATTERNS = (
    r"\b(hola|holaa|holaaa|buenos dias|buenos días|buenas tardes|buenas noches|cordial saludo)\b",
    r"\b(hello|hi|hey|good morning|good afternoon|good evening)\b",
    r"\b(gracias|muchas gracias|mil gracias|thank you|thanks|thx)\b",
    r"\b(porfa|por favor|please|pls)\b",
)

SPANISH_STOPWORDS = {
    "para", "como", "cuando", "donde", "puede", "pueden", "segun", "sobre",
    "desde", "dentro", "negocio", "disponible", "disponibles", "cliente",
    "clientes", "producto", "productos", "servicio", "servicios", "hacer",
    "tener", "tienen", "tienes", "quiero", "quisiera", "necesito", "gustaria",
    "gracias", "hola", "buenas", "dias", "tardes", "noches", "favor",
    "informacion", "pregunta", "puedo", "pueden", "cual", "cuales", "cuanto",
    "cuantos", "cuanta", "cuantas", "este", "esta", "estos", "estas",
}

ENGLISH_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "these", "those",
    "there", "their", "your", "you", "are", "can", "could", "would", "should",
    "please", "thanks", "thank", "hello", "hi", "hey", "need", "want", "like",
    "what", "when", "where", "which", "how", "much", "many", "have", "has",
    "business", "customer", "customers", "product", "products", "service",
    "services", "available", "availability", "information", "question",
}

BILINGUAL_SUPPORT_STOPWORDS = SPANISH_STOPWORDS | ENGLISH_STOPWORDS


def strip_urls(text: str) -> str:
    return URL_PATTERN.sub("", text or "")


def strip_emojis(text: str) -> str:
    return EMOJI_PATTERN.sub("", text or "")


def normalize_chat_text(text: Any, *, for_embedding: bool = True) -> str:
    """
    Limpieza centralizada para chats.

    Importante:
    - Para embeddings y LLM NO removemos stopwords ni conectores.
    - Solo quitamos ruido duro: URLs, emojis opcionales, emails/teléfonos,
      espacios raros y rastros técnicos.
    """
    clean = normalize_text(text)

    if not FAQ_CLEANING_ENABLED:
        return clean

    if FAQ_STRIP_URLS:
        clean = strip_urls(clean)

    if FAQ_STRIP_EMOJIS:
        clean = strip_emojis(clean)

    clean = EMAIL_PATTERN.sub("[correo]", clean)
    clean = PHONE_PATTERN.sub("[telefono]", clean)
    clean = TIME_PATTERN.sub("", clean)
    clean = DATE_PATTERN.sub("", clean)

    for pattern in CHAT_NOISE_PATTERNS:
        clean = re.sub(pattern, "", clean, flags=re.IGNORECASE)

    clean = re.sub(r"\s+([,.;:])", r"\1", clean)
    clean = normalize_text(clean.strip(" -:;,.!?¡¿"))

    return clean


def support_tokens_multilingual(text: str) -> set[str]:
    folded = fold_text(text)
    return {
        token
        for token in re.findall(r"[a-záéíóúñA-ZÁÉÍÓÚÑ]{4,}", folded)
        if token not in BILINGUAL_SUPPORT_STOPWORDS and not token.isdigit()
    }

def word_count(text: str) -> int:
    return len(re.findall(r"\b[\wáéíóúñÁÉÍÓÚÑ]+\b", normalize_text(text), flags=re.UNICODE))


def current_embedding_model_label() -> str:
    return EMBEDDING_BACKEND_READY if EMBEDDING_BACKEND_READY != "hash" else "hash"


def has_emoji(text: str) -> bool:
    return bool(EMOJI_PATTERN.search(text or ""))


def is_internal_tool_trace(text: str) -> bool:
    folded = fold_text(text)
    return any(
        fragment in folded
        for fragment in (
            "reasoning",
            "function_call",
            "function call",
            "tool_call",
            "tool call",
            "getschedules",
            "get schedules",
            "assistant to=functions",
            "arguments",
        )
    )


def is_low_quality_chat_style(text: str) -> bool:
    folded = fold_text(text)
    if has_emoji(text):
        return True
    chat_fragments = (
        "tu toxica de confianza",
        "mi team humano",
        "te paso",
        "te envio",
        "te comparto",
        "me compartes",
        "me puedes compartir",
        "enviame",
        "envianos",
        "mandame",
        "me mandas",
        "indicanos",
        "escribenos",
        "te gustaria",
        "aqui tienes",
        "para que ciudad",
        "que ciudad",
        "mi nombre es",
        "estoy aqui para ayudarte",
        "estoy para ayudarte",
        "dejartelo confirmado",
        "dejarte confirmado",
        "soporte de pago",
        "soporte para dejar",
        "porfa",
        "por favor escribenos",
        "gracias por escribir",
        "feliz dia",
        "bendiciones",
    )
    if any(fragment in folded for fragment in chat_fragments):
        return True
    if re.search(r"(?i)^(hola|buenos dias|buenas tardes|buenas noches|cordial saludo)\b", normalize_text(text)):
        return True
    return False


def has_case_specific_time_reference(text: str) -> bool:
    folded = fold_text(text)
    temporal_words = (
        "hoy",
        "manana",
        "ayer",
        "pasado manana",
        "esta tarde",
        "esta noche",
        "este jueves",
        "este viernes",
        "lunes",
        "martes",
        "miercoles",
        "jueves",
        "viernes",
        "sabado",
        "domingo",
    )
    return bool(TIME_PATTERN.search(text) or DATE_PATTERN.search(text) or any(word in folded for word in temporal_words))


def clean_question_evidence(text: str) -> str:
    clean = redact_personal_data(text)
    clean = normalize_chat_text(clean, for_embedding=False)
    clean = re.sub(
        r"\b(?:hoy|mañana|manana|ayer|lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo)\b",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"\b(?:today|tomorrow|yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    return normalize_text(clean.strip(" -:;,.!?¡¿"))


def clean_answer_evidence(text: str, company_name: Optional[str] = None) -> str:
    if is_internal_tool_trace(text):
        return ""

    clean = clean_faq_answer(text, company_name=company_name)
    clean = normalize_chat_text(clean, for_embedding=False)
    clean = PLACEHOLDER_PATTERN.sub("", clean)
    return normalize_text(clean)


def is_answer_evidence_usable(text: str) -> bool:
    clean = normalize_text(text)
    if len(clean) < 20:
        return False
    if is_internal_tool_trace(clean) or is_low_quality_chat_style(clean):
        return False
    if PLACEHOLDER_PATTERN.search(clean):
        return False
    return True


def is_valid_canonical_question(question: str) -> bool:
    """
    Valida forma y limpieza de una pregunta FAQ.

    Importante:
    NO valida por lista cerrada de temas ni por inicios permitidos.
    Una FAQ válida puede empezar de muchas formas:
    - ¿Ofrecen...?
    - ¿Hacen...?
    - ¿Existe...?
    - ¿Se puede...?
    - ¿Incluyen...?

    Esta función solo debe responder:
    ¿Parece una pregunta FAQ limpia, corta y reutilizable?
    La alineación con el cluster se valida en otra función.
    """
    clean = normalize_text(question)
    folded = fold_text(clean)
    count = word_count(clean)

    if not clean:
        return False

    if len(clean) < 8 or len(clean) > 180:
        return False

    if count < 3 or count > FAQ_MAX_CANONICAL_QUESTION_WORDS:
        return False

    has_question_shape = clean.startswith("¿") or clean.endswith("?") or "?" in clean
    if not has_question_shape:
        return False

    if is_internal_tool_trace(clean):
        return False

    if PLACEHOLDER_PATTERN.search(clean):
        return False

    if has_case_specific_time_reference(clean):
        return False

    if has_emoji(clean):
        return False

    greeting_starts = (
        "hola",
        "holaa",
        "holaaa",
        "buenos dias",
        "buenos días",
        "buenas tardes",
        "buenas noches",
        "como estas",
        "cómo estas",
        "cómo estás",
    )
    if folded.startswith(greeting_starts):
        return False

    raw_chat_starts = (
        "quisiera ",
        "quiero ",
        "necesito ",
        "me gustaria ",
        "me gustaría ",
        "me ayudas ",
        "me puedes ",
        "me podrias ",
        "me podrías ",
        "te pregunto ",
        "una pregunta ",
        "oye ",
        "hey ",
        "porfa ",
        "por favor ",
    )
    if folded.startswith(raw_chat_starts):
        return False

    raw_markers = (
        "mi pedido",
        "mi compra",
        "mi orden",
        "ya hice",
        "ya pague",
        "ya pagué",
        "acabo de",
        "te envie",
        "te envié",
        "me llegaria",
        "me llegaría",
        "me llega",
        "para mi",
        "para mí",
        "para mi novio",
        "para mi novia",
        "para mi mama",
        "para mi mamá",
        "para mi esposo",
        "para mi esposa",
    )
    if any(marker in folded for marker in raw_markers):
        return False

    # Evita preguntas que siguen siendo mensajes específicos de WhatsApp.
    if re.search(r"\b(yo|me|mi|mis|conmigo|con mi)\b", folded):
        return False

    # Evita signos raros o texto roto.
    alpha_count = len(re.findall(r"[a-záéíóúñ]", folded))
    if alpha_count < 6:
        return False

    return True

def is_valid_canonical_answer(answer: str) -> bool:
    clean = normalize_text(answer)
    folded = fold_text(clean)
    count = word_count(clean)
    if not 20 <= len(clean) <= 420:
        return False
    if count < 5 or count > FAQ_MAX_CANONICAL_ANSWER_WORDS:
        return False
    if is_internal_tool_trace(clean) or is_low_quality_chat_style(clean):
        return False
    if PLACEHOLDER_PATTERN.search(clean) or has_emoji(clean):
        return False
    if RAW_URL_PATTERN.search(clean):
        return False
    if has_case_specific_time_reference(clean):
        return False
    if "?" in clean:
        return False
    forbidden_fragments = (
        "tu toxica de confianza",
        "enviame",
        "envianos",
        "mandame",
        "me compartes",
        "me puedes compartir",
        "te paso",
        "te comparto",
        "whatsapp para obtener mas informacion",
        "atencion al cliente para obtener mas informacion",
        "mi nombre es",
        "aqui tienes",
        "para que ciudad",
        "soporte de pago",
        "no hay respuestas historicamente confirmadas",
        "no hay respuestas historicas confirmadas",
        "evidencia historica",
        "historicamente confirmadas",
        "por favor indique",
        "por favor indicanos",
        "por favor indica",
        "consulte el sitio web",
        "contacte al servicio",
        "servicio de atencion al cliente",
        "para informacion mas detallada",
    )
    if any(fragment in folded for fragment in forbidden_fragments):
        return False
    if re.search(r"\b(indica|indique|contacta|contacte|consulta|consulte)\b", folded):
        return False
    if folded.startswith(("claro", "aqui")):
        return False
    if re.search(r"\b(tienes|tengo|quieres|quiero|gustaria)\b", folded):
        return False
    return True


def is_valid_knowledge_statement(statement: str) -> bool:
    clean = normalize_text(statement)
    if not clean:
        return False
    if "?" in clean:
        return False
    if word_count(clean) < 4 or word_count(clean) > 45:
        return False
    if is_internal_tool_trace(clean) or PLACEHOLDER_PATTERN.search(clean) or has_emoji(clean):
        return False
    return True


def clean_statement(text: str, company_name: Optional[str] = None) -> str:
    clean = clean_faq_answer(text, company_name=company_name)
    clean = normalize_text(clean)
    return clean


def is_valid_cluster_intent_statement(statement: str) -> bool:
    clean = normalize_text(statement)
    if not clean or "?" in clean:
        return False
    if word_count(clean) < 4 or word_count(clean) > 55:
        return False
    if is_internal_tool_trace(clean) or PLACEHOLDER_PATTERN.search(clean) or has_emoji(clean):
        return False
    return True


def is_question_answer_aligned(question: str, answer: str, knowledge_statement: str = "") -> Tuple[bool, str]:
    q = fold_text(question)
    a = fold_text(f"{answer} {knowledge_statement}")
    if not q or not a:
        return False, "rejected_question_answer_misaligned"

    shipping_terms = ("domicilio", "envio", "entrega")
    if any(term in a for term in shipping_terms) and "gratis" in a:
        if "gratis" not in q and not any(term in q for term in ("desde", "valor minimo", "monto minimo")):
            return False, "rejected_question_answer_misaligned"
    if any(term in q for term in ("precio", "cuanto cuesta", "cuanto vale", "valor")) and "gratis" in a:
        if "gratis" not in q and not any(term in q for term in ("desde", "valor minimo", "monto minimo")):
            return False, "rejected_question_answer_misaligned"

    q_tokens = set(re.findall(r"\w{4,}", q))
    a_tokens = set(re.findall(r"\w{4,}", a))
    if q_tokens and a_tokens and q_tokens.isdisjoint(a_tokens):
        return False, "rejected_question_answer_misaligned"
    return True, ""


SUPPORT_STOPWORDS = {
    "para",
    "como",
    "cuando",
    "donde",
    "puede",
    "pueden",
    "segun",
    "sobre",
    "desde",
    "dentro",
    "negocio",
    "disponible",
    "disponibles",
    "configuracion",
    "comercial",
    "condiciones",
    "opciones",
    "cliente",
    "clientes",
    "respuesta",
    "pregunta",
    "esta",
    "este",
    "modalidad",
    "habilitada",
    "habilitado",
    "quiero",
    "quisiera",
    "necesito",
    "interesa",
    "gustaria",
    "hola",
    "buen",
    "buenos",
    "buenas",
    "dia",
    "dias",
    "casualidad",
    "preguntar",
    "proceso",
    "hacer",
    "algun",
    "aqui",
    "mandartelo",
    "solo",
    "tienda",
    "virtual",
    "uncle",
    "best",
    "ever",
    "puedo",
    "pueden",
    "tienen",
    "tiene",
    "saber",
    "mostrar",
    "muestra",
    "favor",
    "gracias",
}


def support_tokens(text: str) -> set[str]:
    return support_tokens_multilingual(text)


def deduplicate_support_example_texts(examples: Iterable[str], limit: int = 3) -> Tuple[List[str], int]:
    unique_examples: List[str] = []
    seen: set[str] = set()
    duplicate_count = 0
    for example in examples:
        clean = clean_question_evidence(example)
        if not clean:
            continue
        normalized = normalize_question(clean)
        if normalized in seen:
            duplicate_count += 1
            continue
        seen.add(normalized)
        unique_examples.append(clean)
        if len(unique_examples) >= limit:
            break
    return unique_examples, duplicate_count


def select_support_examples_for_candidate(
    canonical_question: str,
    example_records: List[Dict[str, Any]],
    limit: int = 3,
) -> Tuple[List[str], int]:
    question_tokens = support_tokens(canonical_question)
    ranked: List[Tuple[int, float, int, str]] = []
    seen: set[str] = set()
    duplicate_count = 0
    for position, example in enumerate(example_records):
        clean = clean_question_evidence(example.get("original_user_message", ""))
        if not clean:
            continue
        normalized = normalize_question(clean)
        if normalized in seen:
            duplicate_count += 1
            continue
        seen.add(normalized)
        example_tokens = support_tokens(clean)
        overlap = len(question_tokens & example_tokens)
        similarity = float(example.get("similarity_score") or 0.0)
        ranked.append((overlap, similarity, -position, clean))

    ranked.sort(reverse=True)
    return [item[3] for item in ranked[:limit]], duplicate_count


def is_prudent_answer(answer: str) -> bool:
    folded = fold_text(answer)
    prudent_fragments = (
        "puede variar",
        "pueden variar",
        "depende",
        "debe confirmarse",
        "deben confirmarse",
        "se debe confirmar",
        "requiere revision",
        "cuando esta modalidad este habilitada",
        "cuando el negocio lo tenga habilitado",
        "segun la ubicacion",
        "segun el inventario",
        "segun las condiciones",
        "sin inventar detalles",
    )
    return any(fragment in folded for fragment in prudent_fragments)


def is_generation_supported_by_evidence(
    generation: Dict[str, Any],
    questions: List[str],
    answers: List[str],
) -> Dict[str, Any]:
    """
    Evalúa soporte de evidencia de forma gradual.

    Antes:
    - Si la respuesta no coincidía suficiente con evidencia textual, se descartaba.

    Ahora:
    - strong: candidato sólido.
    - partial: needs_review.
    - weak: question_with_answer_review.
    - none: hard reject solo si no hay conexión útil.
    """
    generated_text = " ".join(
        normalize_text(generation.get(key))
        for key in ("cluster_intent_statement", "knowledge_statement", "canonical_question", "canonical_answer")
    )
    folded_generated = fold_text(generated_text)

    clean_questions = [clean_question_evidence(question) for question in questions]
    clean_questions = [question for question in clean_questions if question]

    clean_answers = [clean_answer_evidence(answer) for answer in answers]
    clean_answers = [answer for answer in clean_answers if answer]

    evidence_text = " ".join(clean_questions + clean_answers)
    folded_evidence = fold_text(evidence_text)

    canonical_question = normalize_text(generation.get("canonical_question"))
    canonical_answer = normalize_text(generation.get("canonical_answer"))

    question_tokens = support_tokens(canonical_question)
    answer_tokens = support_tokens(
        f"{generation.get('knowledge_statement', '')} {canonical_answer}"
    )
    evidence_tokens = support_tokens(evidence_text)
    question_evidence_tokens = support_tokens(" ".join(clean_questions))

    question_overlap_any = bool(question_tokens & evidence_tokens)
    question_overlap_with_questions = bool(question_tokens & question_evidence_tokens)
    prudent_answer = is_prudent_answer(canonical_answer)
    cluster_categories = cluster_intent_categories(clean_questions)

    def support_result(
        support_level: str,
        reason: str,
        should_hard_reject: bool,
        requires_answer_review: bool,
        overlap_ratio: float = 0.0,
    ) -> Dict[str, Any]:
        return {
            "support_level": support_level,
            "reason": reason,
            "should_hard_reject": should_hard_reject,
            "requires_answer_review": requires_answer_review,
            "overlap_ratio": round(overlap_ratio, 4),
        }

    positive_generated = bool(
        re.search(r"\b(si|sí)\b", folded_generated)
        or "se puede" in folded_generated
        or "ofrece" in folded_generated
        or "ofrecemos" in folded_generated
        or "permite" in folded_generated
        or "disponible" in folded_generated
    )
    negative_evidence = bool(
        re.search(
            r"\b(por ahora no|no tenemos|no hacemos|no personalizamos|no recibimos|no contamos|agotado|no disponible)\b",
            folded_evidence,
        )
    )

    if positive_generated and negative_evidence:
        generated_focus = support_tokens(generated_text)
        evidence_focus = support_tokens(evidence_text)
        if generated_focus & evidence_focus:
            return support_result(
                "none",
                "rejected_answer_not_supported_by_evidence",
                True,
                True,
            )

    generated_amounts = set(re.findall(r"\d[\d.,]*", folded_generated))
    evidence_amounts = set(re.findall(r"\d[\d.,]*", folded_evidence))
    if generated_amounts and not generated_amounts.issubset(evidence_amounts):
        if question_overlap_with_questions or cluster_categories:
            return support_result(
                "weak",
                "respuesta con dato numerico a revisar",
                False,
                True,
                0.0,
            )
        return support_result(
            "none",
            "rejected_answer_not_supported_by_evidence",
            True,
            True,
        )

    if answer_tokens:
        overlap_ratio = len(answer_tokens & evidence_tokens) / max(1, len(answer_tokens))

        if overlap_ratio >= 0.55:
            return support_result("strong", "", False, False, overlap_ratio)

        if overlap_ratio >= 0.25:
            return support_result(
                "partial",
                "respuesta con evidencia parcial",
                False,
                True,
                overlap_ratio,
            )

        if overlap_ratio > 0:
            return support_result(
                "weak",
                "pregunta detectada con respuesta a revisar",
                False,
                True,
                overlap_ratio,
            )

    # Caso importante:
    # Si la pregunta del cluster existe, pero la respuesta está débil,
    # se guarda para revisión humana en vez de matarla.
    if question_overlap_with_questions or cluster_categories:
        return support_result(
            "weak",
            "pregunta detectada con respuesta a revisar",
            False,
            True,
            0.0,
        )

    if prudent_answer and question_overlap_any:
        return support_result(
            "weak",
            "respuesta prudente con evidencia limitada",
            False,
            True,
            0.0,
        )

    return support_result(
        "none",
        "rejected_answer_not_supported_by_evidence",
        True,
        True,
        0.0,
    )

def fallback_unpublishable(reason: str, confidence: float = 0.0) -> Dict[str, Any]:
    return {
        "publish": False,
        "cluster_intent_statement": "",
        "knowledge_statement": "",
        "canonical_question": "",
        "canonical_answer": "",
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": reason,
        "mode": "conservative_fallback",
    }


def redact_personal_data(text: str, protected_terms: Optional[List[str]] = None) -> str:
    text = normalize_text(text)
    if not text:
        return ""

    protected_values: Dict[str, str] = {}
    for index, term in enumerate(protected_terms or []):
        clean_term = normalize_text(term)
        if clean_term:
            token = f"__PROTECTED_{index}__"
            protected_values[token] = clean_term
            text = re.sub(re.escape(clean_term), token, text, flags=re.IGNORECASE)

    name_word = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}"
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[correo]", text)
    text = re.sub(r"\b(?:\+?\d[\s().-]?){7,}\b", "[telefono]", text)
    text = re.sub(rf"(?i)\b(hola|buenos dias|buenos días|buenas tardes|buenas noches),?\s+{name_word}(?:\s+{name_word}){{0,3}}", r"\1", text)
    text = re.sub(rf"(?i)\b(gracias por escribir(?:nos)?),?\s+{name_word}(?:\s+{name_word}){{0,3}}", r"\1", text)
    text = re.sub(rf"\b{name_word}(?:\s+{name_word})+\b", "[persona]", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)

    for token, value in protected_values.items():
        text = text.replace(token, value)
    return normalize_text(text)


def clean_faq_answer(answer: str, company_name: Optional[str] = None) -> str:
    protected_terms = [company_name] if company_name else []
    text = redact_personal_data(answer, protected_terms=protected_terms)
    if not text:
        return ""

    text = re.sub(r"(?is)^.*?(respuesta final|faq)\s*:\s*", "", text).strip()
    text = re.sub(r"(?i)^(hola|buenos dias|buenos días|buenas tardes|buenas noches|cordial saludo)[,!¡\s:.-]*", "", text)
    text = re.sub(r"(?i)\bgracias por escribir(?:nos)?[,!¡\s:.-]*", "", text)
    text = re.sub(r"(?i)\b(te ayudamos|te podemos ayudar|podemos ayudarte)\b", "el equipo puede orientar", text)
    text = re.sub(r"(?i)\bte enviaremos\b", "se enviara", text)
    text = re.sub(r"(?i)\bte confirmamos\b", "se confirma", text)
    text = re.sub(r"(?i)\bte recomendamos\b", "se recomienda", text)
    text = re.sub(r"(?i)\b(envianos|envíanos|indicanos|indícanos|escribenos|escríbenos)\b", "se debe informar", text)
    text = re.sub(r"(?i)\btu\s+(dia|día|reserva|clase|plan|pago|inscripcion|inscripción|membresia|membresía)\b", r"el \1", text)
    text = re.sub(r"(?i)\btus\s+(datos|reservas|clases|pagos)\b", r"los \1", text)
    text = re.sub(r"\[persona\],?\s*", "", text)
    text = normalize_text(text.strip(" -:;,."))
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def compact_canonical_answer(answer: str, max_words: int = FAQ_MAX_CANONICAL_ANSWER_WORDS) -> str:
    clean = normalize_text(answer)
    if not clean:
        return ""
    if word_count(clean) <= max_words:
        return clean

    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!])\s+", clean) if sentence.strip()]
    for sentence_count in (1, 2):
        candidate = normalize_text(" ".join(sentences[:sentence_count]))
        if candidate and word_count(candidate) <= max_words:
            return candidate

    words = clean.split()
    compact = " ".join(words[:max_words]).strip(" ,;:")
    if compact and compact[-1] not in ".!?":
        compact += "."
    return compact


def clean_canonical_answer(answer: str, company_name: Optional[str] = None) -> str:
    return compact_canonical_answer(clean_faq_answer(answer, company_name=company_name))


def strip_thinking_text(raw_text: str) -> str:
    text = str(raw_text or "")
    text = re.sub(r"(?is)<think>.*?</think>", "", text)
    text = re.sub(r"(?is)^.*?</think>", "", text)
    return normalize_text(text)


def is_good_faq_candidate(text: str, mode: Optional[str] = None) -> bool:
    text = normalize_chat_text(text, for_embedding=True)
    folded = fold_text(text).strip(" ¿?¡!.,;:")
    word_count_value = len(text.split())
    harvest_mode = (mode or FAQ_CANDIDATE_HARVEST_MODE or "broad").lower()

    if len(text) < 8 or word_count_value < 2 or len(text) > 280:
        return False

    if EMAIL_PATTERN.search(text) or PHONE_PATTERN.search(text):
        return False

    trivial = {
        "hola", "buenas", "buenos dias", "buenas tardes", "buenas noches",
        "si", "sí", "no", "ok", "dale", "gracias", "muchas gracias",
        "hello", "hi", "hey", "yes", "nope", "ok thanks", "thank you", "thanks",
    }
    if folded in trivial:
        return False

    if not EMOJI_PATTERN.sub("", text).strip():
        return False

    non_faq_fragments = (
        "quien soy", "que hora", "que rol", "hora es actualmente",
        "who am i", "what time", "what role",
    )
    if any(fragment in folded for fragment in non_faq_fragments):
        return False

    interrogative_signals = (
        "?", "¿",
        "como ", "cómo ", "cuanto ", "cuánta ", "cuanta ", "cuantos ", "cuántos ",
        "cual ", "cuál ", "cuales ", "cuáles ", "que ", "qué ", "donde ", "dónde ",
        "cuando ", "cuándo ", "puedo ", "se puede ", "es posible ", "hay ",
        "what ", "how ", "when ", "where ", "which ", "can ", "could ", "do you ",
        "does ", "is there ", "are there ", "is it possible ",
    )

    business_intent_terms = (
        # ES
        "envio", "envío", "domicilio", "entrega", "transportadora", "ciudad",
        "producto", "productos", "catalogo", "catálogo", "precio", "precios",
        "costo", "costos", "valor", "pagar", "pago", "pagos", "nequi",
        "daviplata", "pse", "efecty", "tarjeta", "anticipo", "abono",
        "reserva", "reservar", "agenda", "agendar", "horario", "horarios",
        "disponible", "disponibilidad", "talla", "tallas", "color", "colores",
        "personalizar", "personalizacion", "personalización", "logo",
        "descuento", "promo", "promocion", "promoción", "pedido", "pedidos",
        "recoger", "recogida", "punto fisico", "punto físico",
        # EN
        "shipping", "delivery", "pickup", "address", "city", "order", "orders",
        "product", "products", "catalog", "price", "prices", "cost", "costs",
        "payment", "pay", "card", "cash", "deposit", "advance", "booking",
        "reservation", "schedule", "availability", "available", "size", "sizes",
        "color", "colors", "custom", "customize", "personalized", "logo",
        "discount", "promotion", "promo",
    )

    has_question_signal = any(signal in folded for signal in interrogative_signals)
    has_business_intent = any(term in folded for term in business_intent_terms)

    if harvest_mode == "strict":
        return has_question_signal and has_business_intent

    return has_question_signal or has_business_intent

def company_key(item: Dict[str, Any]) -> str:
    return normalize_text(str(item.get("company_id") or item.get("workspace_id") or "unknown"))


def most_common_answer(answers: List[str], protected_terms: Optional[List[str]] = None) -> str:
    clean_answers = []
    for answer in answers:
        clean = clean_faq_answer(redact_personal_data(answer, protected_terms=protected_terms))
        if clean and len(clean) >= 25:
            clean_answers.append(clean)
    if clean_answers:
        return Counter(clean_answers).most_common(1)[0][0]
    return "La informacion disponible no es suficiente para publicar una respuesta definitiva; se debe confirmar el procedimiento con el equipo responsable."


def clean_canonical_question(question: str) -> str:
    clean = clean_question_evidence(question)
    folded = fold_text(clean).strip(" ?")
    replacements = [
        (r"^(quisiera|quiero|necesito)\s+agendar\s+", "¿Cómo puedo agendar "),
        (r"^(quisiera|quiero|necesito)\s+reservar\s+", "¿Cómo puedo reservar "),
        (r"^(puedo|se puede)\s+pagar\s+con\s+la\s+mitad\b.*", "¿Es posible reservar pagando un anticipo?"),
        (r"^(como|cómo)\s+hago\s+para\s+pagar\s+por\s+", "¿Cómo puedo pagar por "),
        (r"^y?\s*(que|qué)\s+precio\s+tiene\s+el\s+", "¿Cuánto cuesta el "),
        (r"^cuanto\s+(vale|cuesta)\s+el\s+", "¿Cuánto cuesta el "),
    ]
    for pattern, replacement in replacements:
        if re.search(pattern, folded, flags=re.IGNORECASE):
            clean = re.sub(pattern, replacement, clean, flags=re.IGNORECASE)
            break
    clean = normalize_text(clean.strip(" -:;,.¿?"))
    if not clean:
        return ""
    if not clean.startswith("¿"):
        clean = f"¿{clean[0].upper()}{clean[1:]}"
    if not clean.endswith("?"):
        clean = f"{clean}?"
    return clean


def extract_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    text = strip_thinking_text(raw_text)
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def parse_llm_faq_candidate(raw_text: str) -> Optional[Dict[str, Any]]:
    payload = extract_json_object(raw_text)
    if payload is None:
        print("Qwen devolvio JSON invalido o ausente.")
        return None

    required = {"publish", "canonical_question", "canonical_answer", "confidence", "reason"}
    if not required.issubset(payload):
        print(f"Qwen devolvio JSON sin claves requeridas: {sorted(payload.keys())}")
        return None
    if not isinstance(payload["publish"], bool):
        print("Qwen devolvio publish con tipo invalido.")
        return None
    if not isinstance(payload["canonical_question"], str) or not isinstance(payload["canonical_answer"], str):
        print("Qwen devolvio pregunta/respuesta con tipo invalido.")
        return None
    if not isinstance(payload["reason"], str):
        print("Qwen devolvio reason con tipo invalido.")
        return None
    if "knowledge_statement" in payload and not isinstance(payload["knowledge_statement"], str):
        print("Qwen devolvio knowledge_statement con tipo invalido.")
        return None
    if "cluster_intent_statement" in payload and not isinstance(payload["cluster_intent_statement"], str):
        print("Qwen devolvio cluster_intent_statement con tipo invalido.")
        return None
    try:
        confidence = float(payload["confidence"])
    except (TypeError, ValueError):
        print("Qwen devolvio confidence no numerico.")
        return None
    if not 0 <= confidence <= 1:
        print(f"Qwen devolvio confidence fuera de rango: {confidence}")
        return None

    confidence_was_repaired = False
    reason_text = normalize_text(payload["reason"])
    if payload["publish"] and confidence == 0.0:
        folded_reason = fold_text(reason_text)
        if any(fragment in folded_reason for fragment in ("intencion es clara", "evidencia respalda", "evidencia es directa")):
            confidence = 0.45
            confidence_was_repaired = True

    return {
        "publish": payload["publish"],
        "cluster_intent_statement": clean_statement(payload.get("cluster_intent_statement") or ""),
        "knowledge_statement": clean_canonical_answer(
            payload.get("knowledge_statement") or payload.get("canonical_answer") or ""
        ),
        "canonical_question": clean_canonical_question(payload["canonical_question"]),
        "canonical_answer": clean_canonical_answer(payload["canonical_answer"]),
        "confidence": confidence,
        "reason": reason_text,
        "mode": "llm_json",
        "confidence_was_repaired": confidence_was_repaired,
    }

def validate_generated_candidate(candidate: Dict[str, Any]) -> Tuple[bool, str]:
    if not candidate.get("publish"):
        return False, "llm_publish_false"

    if float(candidate.get("confidence") or 0) < FAQ_MIN_GENERATION_CONFIDENCE:
        return False, "rejected_generation_confidence"

    if not is_valid_knowledge_statement(candidate.get("knowledge_statement", "")):
        fallback_statement = normalize_text(candidate.get("canonical_answer", ""))
        if is_valid_knowledge_statement(fallback_statement):
            candidate["knowledge_statement"] = fallback_statement
        else:
            return False, "rejected_invalid_knowledge_statement"

    if not is_valid_canonical_question(candidate.get("canonical_question", "")):
        return False, "rejected_invalid_canonical_question"

    if not is_valid_canonical_answer(candidate.get("canonical_answer", "")):
        return False, "rejected_invalid_canonical_answer"

    aligned, reason = is_question_answer_aligned(
        candidate.get("canonical_question", ""),
        candidate.get("canonical_answer", ""),
        candidate.get("knowledge_statement", ""),
    )
    if not aligned:
        return False, reason

    return True, ""

def classify_quality_tier(
    generation: Dict[str, Any],
    cluster_cohesion: float,
    support: float,
    valid_answer_count: int,
    review_reasons: Optional[List[str]] = None,
) -> Tuple[str, Optional[str]]:
    reasons = list(review_reasons or [])
    confidence = float(generation.get("confidence") or 0.0)
    if confidence < FAQ_HIGH_CONFIDENCE_THRESHOLD:
        reasons.append("confianza media del LLM")
    if cluster_cohesion < FAQ_MIN_CLUSTER_COHESION:
        reasons.append("cohesion util pero no alta")
    if support < FAQ_MIN_CLUSTER_SUPPORT:
        reasons.append("soporte semantico medio")
    if valid_answer_count < 1:
        reasons.append("respuesta prudente con poca evidencia historica limpia")

    if reasons:
        return "needs_review", "; ".join(dict.fromkeys(reasons))
    return "high_confidence", None


def build_prudent_answer(questions: List[str], categories: set[str]) -> Optional[str]:
    folded_questions = " ".join(fold_text(question) for question in questions)
    product_focus_counts: Counter[str] = Counter()

    for question in questions:
        product_focus_counts.update(support_tokens(question))

    stop_tokens = {
        "producto",
        "productos",
        "tienen",
        "tienes",
        "manejan",
        "realizan",
        "quiero",
        "quisiera",
        "gustaria",
        "personalizar",
        "pedido",
        "hacer",
    }
    focus_tokens = [
        token
        for token, _count in product_focus_counts.most_common(4)
        if token not in stop_tokens
    ]
    product_focus = " ".join(focus_tokens[:3])

    if "payment" in categories:
        if any(term in folded_questions for term in ("anticipo", "mitad", "abono", "abonando", "separar")):
            return "El pago con anticipo puede estar disponible segun las condiciones del negocio."
        return "Los medios de pago disponibles dependen de la configuracion comercial del negocio."

    if "scheduling" in categories:
        return "La disponibilidad para agendar depende de los horarios definidos por el negocio."

    if "shipping" in categories:
        if "bucaramanga" in folded_questions:
            return "La cobertura de envios a Bucaramanga debe confirmarse segun las condiciones del pedido."
        if "fusagasuga" in folded_questions:
            return "La cobertura de entrega en Fusagasuga debe confirmarse segun la zona del pedido."
        if "gratis" in folded_questions and "domicilio" in folded_questions:
            return "El domicilio gratis puede aplicar segun el valor del pedido y la zona de cobertura."
        return "Las opciones de entrega y sus costos pueden variar segun la ubicacion y las condiciones del pedido."

    if "product_availability" in categories:
        if "personaliz" in folded_questions and "whatsapp" in folded_questions:
            return "La personalizacion por WhatsApp puede variar segun las opciones habilitadas por el negocio."

        if product_focus:
            return f"La disponibilidad de {product_focus} debe confirmarse segun el inventario del negocio."

        if "personaliz" in folded_questions:
            return "La personalizacion de productos puede variar segun las opciones habilitadas por el negocio."

        return "La disponibilidad de productos debe confirmarse segun el inventario del negocio."

    if "pricing" in categories:
        return "El valor puede variar segun las condiciones definidas por el negocio."

    return "La informacion debe revisarse antes de publicar una respuesta definitiva."

def build_prudent_question(questions: List[str], categories: set[str]) -> Optional[str]:
    """
    Genera una pregunta prudente solo como fallback.

    Ojo: esta función sí usa categorías, pero NO para validar.
    Solo se usa cuando el LLM falló o hay que reparar.
    """
    folded_questions = fold_text(" ".join(questions))
    token_counts: Counter[str] = Counter()

    for question in questions:
        token_counts.update(support_tokens(question))

    if "shipping" in categories:
        if "bucaramanga" in folded_questions:
            return "Hacen envios a Bucaramanga?"
        if "fusagasuga" in folded_questions:
            return "La entrega cubre Fusagasuga?"
        if "domicilio" in folded_questions and "gratis" in folded_questions:
            return "Desde que valor el domicilio es gratis?"
        if "domicilio" in folded_questions:
            return "El domicilio tiene costo adicional?"
        if "recog" in folded_questions or "punto fisico" in folded_questions:
            return "Se puede recoger el pedido?"
        return "Hacen envios a otras ciudades?"

    if "payment" in categories:
        if any(term in folded_questions for term in ("anticipo", "mitad", "abono", "abonando", "separar")):
            return "Se puede pagar con anticipo?"
        return "Que medios de pago aceptan?"

    if "scheduling" in categories:
        if "agenda" in folded_questions:
            return "La agenda esta disponible para pedidos?"
        return "Como se puede agendar un pedido?"

    if "pricing" in categories:
        if "envio" in folded_questions or "domicilio" in folded_questions:
            return "Cual es el costo del envio?"
        return "Cual es el valor del producto?"

    if "product_availability" in categories:
        if "whatsapp" in folded_questions and "personaliz" in folded_questions:
            return "Como personalizar un producto por WhatsApp?"
        if "punto fisico" in folded_questions:
            return "Tienen punto fisico?"
        if "iman" in folded_questions:
            return "Tienen imanes personalizados?"
        if "superman" in folded_questions or "super girl" in folded_questions or "supergirl" in folded_questions:
            return "Tienen medias de Superman?"
        if "mug" in folded_questions:
            return "Se pueden personalizar mugs?"
        if "marco magnetico" in folded_questions or "marco magnetic" in folded_questions:
            return "Que es el marco magnetico?"
        if "old school" in folded_questions:
            return "Cuales son los productos old school?"
        if "talla" in folded_questions:
            return "Se puede pedir una talla personalizada?"
        if "dia de la mujer" in folded_questions:
            return "Tienen productos para el dia de la mujer?"
        if "promo" in folded_questions or "promocion" in folded_questions:
            return "Tienen promociones disponibles?"
        if "producto" in folded_questions and "aparece" in folded_questions:
            return "Que hacer si un producto no aparece disponible?"
        if "box mujer" in folded_questions:
            return "Tienen box mujer disponible?"
        if "box hombre" in folded_questions:
            return "Tienen box hombre disponible?"

        stop_tokens = {
            "producto",
            "productos",
            "tienen",
            "tienes",
            "manejan",
            "realizan",
            "quiero",
            "quisiera",
            "gustaria",
            "personalizar",
            "pedido",
            "hacer",
        }
        focus_tokens = [
            token
            for token, _count in token_counts.most_common(4)
            if token not in stop_tokens
        ]
        if focus_tokens:
            return f"Tienen {' '.join(focus_tokens[:3])} disponible?"

        return "Tienen productos disponibles?"

    return None

def recover_unsupported_generation_for_review(
    generation: Dict[str, Any],
    cluster_questions: List[str],
    intent_categories: set[str],
    question_embeddings: List[List[float]],
    cluster_center: List[float],
) -> Optional[Dict[str, Any]]:
    if not generation.get("publish"):
        return None
    if not intent_categories:
        return None

    prudent_answer = build_prudent_answer(cluster_questions, intent_categories)
    if not prudent_answer or not is_valid_canonical_answer(prudent_answer):
        return None

    question = normalize_text(generation.get("canonical_question", ""))
    if is_valid_canonical_question(question):
        alignment = is_canonical_question_aligned_with_cluster(
            question,
            cluster_questions,
            question_embeddings=question_embeddings,
            cluster_center=cluster_center,
        )
    else:
        alignment = {"status": "misaligned"}
    if alignment["status"] == "misaligned":
        question = build_prudent_question(cluster_questions, intent_categories) or ""
        if not is_valid_canonical_question(question):
            return None
        alignment = is_canonical_question_aligned_with_cluster(
            question,
            cluster_questions,
            question_embeddings=question_embeddings,
            cluster_center=cluster_center,
        )
        if alignment["status"] == "misaligned":
            return None

    recovered = dict(generation)
    recovered["canonical_question"] = question
    recovered["canonical_answer"] = prudent_answer
    recovered["knowledge_statement"] = prudent_answer
    if not is_valid_cluster_intent_statement(recovered.get("cluster_intent_statement", "")):
        recovered["cluster_intent_statement"] = derive_cluster_intent_statement(cluster_questions)
    recovered["confidence"] = min(float(generation.get("confidence") or 0.0), 0.62)
    recovered["reason"] = normalize_text(
        f"{generation.get('reason', '')} Respuesta reemplazada por una formulacion prudente para revision humana por soporte historico insuficiente."
    )
    recovered["mode"] = f"{generation.get('mode', 'unknown')}_unsupported_answer_recovered"
    recovered["requires_answer_review"] = True
    return recovered


def apply_chat_template_no_thinking(tokenizer: Any, messages: List[Dict[str, str]]) -> str:
    kwargs = {"tokenize": False, "add_generation_prompt": True}
    if FAQ_LLM_THINKING_DISABLED:
        kwargs["enable_thinking"] = False
    try:
        return tokenizer.apply_chat_template(messages, **kwargs)
    except TypeError:
        kwargs.pop("enable_thinking", None)
        return tokenizer.apply_chat_template(messages, **kwargs)


def generate_faq_candidate_with_llm(
    company_name: Optional[str],
    questions: List[str],
    historical_answers: List[str],
    cluster_metrics: Dict[str, Any],
    recurrence: int,
) -> Dict[str, Any]:
    load_answer_generator()
    clean_questions = [clean_question_evidence(question) for question in questions]
    clean_questions = [question for question in clean_questions if question and is_good_faq_candidate(question)]

    clean_answers = [clean_answer_evidence(answer, company_name=company_name) for answer in historical_answers]
    clean_answers = [answer for answer in clean_answers if is_answer_evidence_usable(answer)]

    if not ANSWER_GENERATOR:
        return fallback_unpublishable("LLM local no disponible; fallback conservador no publica candidatos.", 0.0)
    if len(clean_questions) < FAQ_MIN_QUESTION_EVIDENCE:
        return fallback_unpublishable("Evidencia insuficiente de preguntas utiles en el cluster.", 0.2)

    question_context = "\n".join(f"- {question}" for question in clean_questions[:8])
    answer_context = (
        "\n".join(f"- {answer}" for answer in clean_answers[:6])
        if clean_answers
        else "- No hay respuestas historicas limpias; si la intencion es clara, usa una respuesta prudente y general sin inventar datos."
    )

    messages = [
        {
            "role": "system",
            "content": (
                "Eres un normalizador de conocimiento empresarial, no un chatbot. "
                "Analiza patrones repetidos y sintetiza una FAQ reutilizable para futuros clientes. "
                "No copies textualmente mensajes ni respuestas historicas. Convierte variaciones concretas en formulaciones generales. "
                "No uses saludos, nombres, emojis, tono conversacional, preguntas de seguimiento, 'te', 'tu', 'me envias', 'te paso' ni frases casuales de marca. "
                "No uses fechas, horas, referencias temporales, placeholders como [telefono] o [correo], ni trazas internas como Reasoning, function_call, getSchedules o tool_call. "
                "La pregunta FAQ debe ser corta, directa y de una sola oracion, idealmente maximo 18 palabras. "
                "La respuesta FAQ debe ser breve, clara y directa, preferiblemente de 1 o 2 oraciones y entre 12 y 45 palabras. "
                "Primero deriva cluster_intent_statement usando principalmente las preguntas reales del usuario; no lo derives de respuestas del asistente. "
                "Luego deriva una proposicion factual estable en knowledge_statement usando la evidencia historica. "
                "La pregunta FAQ debe estar anclada a cluster_intent_statement y tambien ser consistente con knowledge_statement. "
                "No cambies la pregunta a un tema que aparece solo como dato colateral en respuestas historicas. "
                "Si las preguntas reales son sobre un producto, la pregunta FAQ debe tratar ese producto; si son sobre domicilio, puede tratar domicilio. "
                "Si la evidencia dice que el domicilio es gratis desde cierto valor y las preguntas reales preguntan por domicilio gratis o costo de domicilio, pregunta por el domicilio gratis o por el valor desde el que aplica. "
                "Evita parrafos largos y explicaciones comerciales extensas. "
                "Devuelve publish=false solo si el cluster es ruido real, no tiene ninguna intencion FAQ reconocible, o no existe forma minima de proponer una pregunta revisable. "
                "Si la pregunta del cliente es clara pero la respuesta historica es incompleta, usa publish=true con una respuesta prudente y marca baja confianza. "
                "Recuerda que esto es un sistema de sugerencias para revision humana, no autopublicacion perfecta. "
                "Si la intencion es clara pero faltan detalles exactos, genera una respuesta cautelosa y general; no inventes precios, horarios ni reglas especificas. "
                "Responde SOLO JSON valido, sin markdown ni texto adicional, con claves exactas: publish, cluster_intent_statement, knowledge_statement, canonical_question, canonical_answer, confidence, reason. "
                "No incluyas bloques <think>, razonamiento interno ni texto fuera del JSON."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Empresa/workspace: {company_name or 'N/D'}\n"
                f"Recurrencia: {recurrence}\n"
                f"Metricas del cluster: {json.dumps(cluster_metrics, ensure_ascii=False)}\n\n"
                f"Preguntas reales limpias:\n{question_context}\n\n"
                f"Respuestas historicas limpias:\n{answer_context}\n\n"
                "Devuelve JSON. Usa publish=true si existe una intencion FAQ clara o una pregunta recurrente revisable, incluso si la respuesta requiere validacion humana. "
                "Solo usa publish=false para ruido, contradiccion fuerte, cluster imposible de interpretar o ausencia total de intencion FAQ. "
                "Usa exactamente estas claves: publish, cluster_intent_statement, knowledge_statement, canonical_question, canonical_answer, confidence, reason. "
                "confidence debe ser un numero JSON real entre 0.20 y 0.95 cuando publish=true."
            ),
        },
    ]

    try:
        tokenizer = ANSWER_GENERATOR.tokenizer
        prompt = apply_chat_template_no_thinking(tokenizer, messages)
        started_at = time.perf_counter()
        generated = ANSWER_GENERATOR(
            prompt,
            max_new_tokens=260,
            do_sample=False,
            return_full_text=False,
            pad_token_id=tokenizer.eos_token_id,
        )
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        raw_generated_text = generated[0].get("generated_text", "")
        parsed = parse_llm_faq_candidate(raw_generated_text)
        if parsed is None:
            return fallback_unpublishable("Qwen devolvio JSON invalido.", 0.0)
        if not is_valid_cluster_intent_statement(parsed.get("cluster_intent_statement", "")):
            parsed["cluster_intent_statement"] = derive_cluster_intent_statement(clean_questions)
        if not parsed["publish"]:
            print(f"Qwen descarto cluster: {parsed.get('reason')}")
        parsed["raw_generation"] = raw_generated_text
        parsed["prompt"] = prompt
        parsed["generation_elapsed_ms"] = elapsed_ms
        return parsed
    except Exception as exc:
        print(f"Qwen fallo durante la sintesis del candidato FAQ: {exc}")
        return fallback_unpublishable(f"Error de generacion LLM: {exc}", 0.0)


def repair_faq_candidate_with_llm(
    company_name: Optional[str],
    generation: Dict[str, Any],
    rejection_reason: str,
    questions: List[str],
    historical_answers: List[str],
    cluster_metrics: Dict[str, Any],
    cluster_intent_statement: Optional[str] = None,
    alignment_reason: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    load_answer_generator()
    if not ANSWER_GENERATOR:
        return None

    evidence_questions = [clean_question_evidence(question) for question in questions]
    evidence_questions = [question for question in evidence_questions if question][:8]
    resolved_intent_statement = (
        cluster_intent_statement
        if is_valid_cluster_intent_statement(cluster_intent_statement or "")
        else derive_cluster_intent_statement(evidence_questions)
    )
    evidence_answers = [clean_answer_evidence(answer, company_name=company_name) for answer in historical_answers]
    evidence_answers = [answer for answer in evidence_answers if is_answer_evidence_usable(answer)][:6]
    answer_context = (
        "\n".join(f"- {answer}" for answer in evidence_answers)
        if evidence_answers
        else "- No hay respuestas historicas limpias; si la pregunta es clara, conserva una respuesta prudente sin inventar detalles."
    )

    messages = [
        {
            "role": "system",
            "content": (
                "Eres un revisor de calidad de FAQs empresariales. "
                "Repara solo lo necesario para que la pregunta, respuesta y proposicion queden alineadas con la evidencia. "
                "La pregunta FAQ debe mantenerse anclada a cluster_intent_statement, que proviene de preguntas reales del usuario. "
                "No conviertas datos colaterales de respuestas historicas en el tema principal de la pregunta FAQ. "
                "Si faltan detalles, usa una respuesta prudente y marca la FAQ como revisable; no inventes precios, horarios ni reglas. "
                "Devuelve SOLO JSON valido con: publish, cluster_intent_statement, knowledge_statement, canonical_question, canonical_answer, confidence, reason, repaired."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Empresa/workspace: {company_name or 'N/D'}\n"
                f"Razon de rechazo: {rejection_reason}\n"
                f"Razon de desalineacion: {alignment_reason or 'N/D'}\n"
                f"Metricas del cluster: {json.dumps(cluster_metrics, ensure_ascii=False)}\n\n"
                f"Cluster intent statement: {resolved_intent_statement}\n\n"
                f"Pregunta actual: {generation.get('canonical_question', '')}\n"
                f"Respuesta actual: {generation.get('canonical_answer', '')}\n"
                f"Knowledge statement actual: {generation.get('knowledge_statement', '')}\n\n"
                f"Preguntas reales limpias:\n" + "\n".join(f"- {question}" for question in evidence_questions) + "\n\n"
                f"Respuestas historicas limpias:\n{answer_context}\n\n"
                "Repara el candidato. Si la intencion recurrente es clara pero la respuesta requiere revision, usa una respuesta cautelosa."
            ),
        },
    ]

    try:
        tokenizer = ANSWER_GENERATOR.tokenizer
        prompt = apply_chat_template_no_thinking(tokenizer, messages)
        generated = ANSWER_GENERATOR(
            prompt,
            max_new_tokens=260,
            do_sample=False,
            return_full_text=False,
            pad_token_id=tokenizer.eos_token_id,
        )
        raw_generated_text = generated[0].get("generated_text", "")
        parsed = parse_llm_faq_candidate(raw_generated_text)
        if parsed is None:
            return None
        if not is_valid_cluster_intent_statement(parsed.get("cluster_intent_statement", "")):
            parsed["cluster_intent_statement"] = resolved_intent_statement
        parsed["mode"] = "llm_repair"
        parsed["repaired"] = True
        parsed["raw_repair_generation"] = raw_generated_text
        return parsed
    except Exception as exc:
        print(f"Qwen fallo durante repair de candidato FAQ: {exc}")
        return None


def can_persist_generation_for_review(
    generation: Dict[str, Any],
    rejection_reason: str,
    intent_categories: set[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Decide si una salida imperfecta se puede guardar para revisión humana.

    La idea:
    - No persistir basura.
    - Sí persistir preguntas claras aunque la respuesta necesite revisión.
    """
    if not generation.get("publish"):
        return False, None, None

    if not intent_categories:
        return False, None, None

    question = normalize_text(generation.get("canonical_question", ""))
    answer = normalize_text(generation.get("canonical_answer", ""))

    question_ok = is_valid_canonical_question(question)
    answer_ok = is_valid_canonical_answer(answer)

    recoverable_reasons = {
        "rejected_invalid_knowledge_statement",
        "rejected_invalid_canonical_answer",
        "rejected_question_answer_misaligned",
        "rejected_answer_not_supported_by_evidence",
    }

    if question_ok and rejection_reason in recoverable_reasons:
        if not answer_ok:
            fallback_answer = build_prudent_answer([question], intent_categories)
            generation["canonical_answer"] = fallback_answer or "La respuesta requiere revisión humana antes de publicarse."

        if not is_valid_knowledge_statement(generation.get("knowledge_statement", "")):
            generation["knowledge_statement"] = generation.get("canonical_answer", "")

        return True, "pregunta detectada con respuesta a revisar", "question_with_answer_review"

    if answer_ok and rejection_reason == "rejected_invalid_canonical_question":
        return False, None, None

    return False, None, None

def load_existing_faqs_by_company() -> Dict[str, List[str]]:
    if not FAQ_SKIP_EXISTING:
        return {}

    query = f"""
        SELECT workspace_id, question
        FROM {FAQ_SCHEMA}.existing_faqs
        WHERE is_active = true
          AND deleted_at IS NULL
          AND question IS NOT NULL
          AND btrim(question) <> ''
    """

    faqs_by_company: Dict[str, List[str]] = defaultdict(list)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                for workspace_id, question in cursor.fetchall():
                    clean = normalize_text(question)
                    if clean:
                        faqs_by_company[str(workspace_id)].append(clean)
    except Exception as exc:
        print(f"No se pudieron cargar FAQs existentes. Se generaran sugerencias sin deduplicar. Error: {exc}")
    return faqs_by_company


def load_previous_candidates_by_company() -> Dict[str, List[str]]:
    """
    Carga candidatos previos para evitar duplicidad entre corridas.

    Importante:
    Incluye rejected cuando FAQ_SKIP_REJECTED=true, porque el cliente pidió que
    las FAQs rechazadas no vuelvan a generarse en siguientes corridas.
    """
    statuses = ["pending", "approved", "needs_review"]
    if FAQ_SKIP_REJECTED:
        statuses.append("rejected")

    placeholders = ", ".join(["%s"] * len(statuses))
    query = f"""
        SELECT workspace_id, normalized_question
        FROM {FAQ_SCHEMA}.faq_candidates
        WHERE status IN ({placeholders})
          AND normalized_question IS NOT NULL
          AND btrim(normalized_question) <> ''
    """

    candidates_by_company: Dict[str, List[str]] = defaultdict(list)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, tuple(statuses))
                for workspace_id, question in cursor.fetchall():
                    clean = normalize_text(question)
                    if clean:
                        candidates_by_company[str(workspace_id)].append(clean)

    except Exception as exc:
        print(f"No se pudieron cargar candidatos previos para deduplicacion. Error: {exc}")

    return candidates_by_company

def is_existing_faq(question: str, existing_questions: List[str]) -> bool:
    if not existing_questions:
        return False

    normalized = normalize_question(question)
    if normalized in {normalize_question(existing) for existing in existing_questions}:
        return True

    embeddings = encode_texts([question, *existing_questions])
    similarities = [_dot(existing_embedding, embeddings[0]) for existing_embedding in embeddings[1:]]
    return bool(similarities and max(similarities) >= FAQ_DUPLICATE_THRESHOLD)


def _most_common_agent_id(items: Iterable[Dict[str, Any]]) -> Optional[str]:
    values = [str(item["agent_id"]) for item in items if item.get("agent_id")]
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def cluster_intent_categories(questions: List[str]) -> set[str]:
    categories: set[str] = set()
    for question in questions:
        folded = fold_text(question)
        payment_like = any(term in folded for term in ("pagar", "pago", "nequi", "daviplata", "pse", "efecty", "anticipo", "mitad", "tarjeta", "abono", "abonando"))
        scheduling_like = any(term in folded for term in ("agendar", "clase", "cortesia", "prueba", "horario", "agenda"))
        if "reserv" in folded and not payment_like:
            scheduling_like = True
        if scheduling_like:
            categories.add("scheduling")
        if payment_like:
            categories.add("payment")
        if any(term in folded for term in ("envio", "domicilio", "transportadora", "entrega", "prioritario", "ciudad", "chia", "fusagasuga", "mosquera", "suba")):
            categories.add("shipping")
        product_like = any(
            term in folded
            for term in (
                "desayuno",
                "sorpresa",
                "producto",
                "productos",
                "media",
                "medias",
                "ropa",
                "personaliz",
                "talla",
                "tallas",
                "tienen",
                "manejan",
                "box",
                "mug",
                "catalogo",
                "catalogos",
                "camiseta",
                "camisetas",
                "estampado",
                "estampados",
                "estampada",
                "estampadas",
                "iman",
                "imanes",
                "superman",
                "super",
                "trae",
                "lleva",
                "hombre",
                "mujer",
            )
        )
        if product_like:
            categories.add("product_availability")
        if any(term in folded for term in ("precio", "cuanto", "vale", "cuesta", "costo")):
            categories.add("pricing")
    return categories


def is_mixed_intent_cluster(questions: List[str]) -> Tuple[bool, str]:
    categories = cluster_intent_categories(questions)
    primary_categories = categories - {"pricing"}
    if len(primary_categories) > 1:
        return True, f"Cluster mezcla intenciones: {', '.join(sorted(primary_categories))}."
    if "product_availability" in categories and "shipping" in categories:
        return True, "Cluster mezcla disponibilidad de producto con condiciones de envio."
    return False, ""


def derive_cluster_intent_statement(questions: List[str]) -> str:
    clean_questions = [clean_question_evidence(question) for question in questions]
    clean_questions = [question for question in clean_questions if question]
    categories = cluster_intent_categories(clean_questions)
    token_counts: Counter[str] = Counter()
    for question in clean_questions:
        token_counts.update(support_tokens(question))
    focus_tokens = [token for token, _count in token_counts.most_common(4)]
    focus = " ".join(focus_tokens)

    shipping_focus_terms = {
        "domicilio",
        "domicilios",
        "envio",
        "envios",
        "entrega",
        "entregas",
        "llega",
        "llegar",
        "ciudad",
    }
    if "shipping" in categories and (
        any(token in token_counts for token in shipping_focus_terms)
        or "product_availability" not in categories
    ):
        return "Los clientes preguntan repetidamente por condiciones de domicilio, envio o entrega."
    if "payment" in categories:
        return "Los clientes preguntan repetidamente por medios de pago, anticipos o reservas."
    if "scheduling" in categories:
        return "Los clientes preguntan repetidamente por agendamiento, horarios o reservas."
    if "product_availability" in categories:
        if focus:
            return f"Los clientes preguntan repetidamente por disponibilidad o caracteristicas de {focus}."
        return "Los clientes preguntan repetidamente por disponibilidad o caracteristicas de productos."
    if "pricing" in categories:
        return "Los clientes preguntan repetidamente por precios, costos o valores."
    if focus:
        return f"Los clientes repiten una intencion relacionada con {focus}."
    return "Los clientes repiten una misma intencion de consulta."


def _primary_alignment_categories(categories: set[str]) -> set[str]:
    return categories - {"pricing"}


def is_canonical_question_aligned_with_cluster(
    canonical_question: str,
    cluster_questions: List[str],
    question_embeddings: Optional[List[List[float]]] = None,
    cluster_center: Optional[List[float]] = None,
) -> Dict[str, Any]:
    clean_question = normalize_text(canonical_question)
    clean_cluster_questions = [clean_question_evidence(question) for question in cluster_questions]
    clean_cluster_questions = [question for question in clean_cluster_questions if question]
    if not clean_question or not clean_cluster_questions:
        return {
            "status": "misaligned",
            "reason": "No hay pregunta canonica o preguntas limpias suficientes para validar alineacion.",
            "centroid_similarity": 0.0,
            "average_similarity": 0.0,
            "token_overlap_ratio": 0.0,
        }

    question_tokens = support_tokens(clean_question)
    cluster_token_counts: Counter[str] = Counter()
    for question in clean_cluster_questions:
        cluster_token_counts.update(support_tokens(question))
    cluster_tokens = set(cluster_token_counts)
    shared_tokens = question_tokens & cluster_tokens
    token_overlap_ratio = round(len(shared_tokens) / max(1, min(len(question_tokens), 5)), 4)

    cluster_categories = cluster_intent_categories(clean_cluster_questions)
    question_categories = cluster_intent_categories([clean_question])
    cluster_primary = _primary_alignment_categories(cluster_categories)
    question_primary = _primary_alignment_categories(question_categories)
    category_overlap = bool(cluster_categories & question_categories)
    category_mismatch = bool(cluster_primary and question_primary and cluster_primary.isdisjoint(question_primary))
    introduced_primary_categories = question_primary - cluster_primary if cluster_primary else set()

    centroid_similarity = 0.0
    average_similarity = 0.0
    try:
        if question_embeddings and cluster_center:
            question_embedding = encode_texts([clean_question])[0]
            centroid_similarity = _dot(question_embedding, cluster_center)
            average_similarity = sum(_dot(question_embedding, embedding) for embedding in question_embeddings) / len(question_embeddings)
        else:
            encoded = encode_texts([clean_question, *clean_cluster_questions])
            question_embedding = encoded[0]
            cluster_embeddings = encoded[1:]
            center = _normalize_vector(_mean_vector(cluster_embeddings))
            centroid_similarity = _dot(question_embedding, center)
            average_similarity = sum(_dot(question_embedding, embedding) for embedding in cluster_embeddings) / len(cluster_embeddings)
    except Exception as exc:
        return {
            "status": "partial_alignment" if shared_tokens or category_overlap else "misaligned",
            "reason": f"No se pudo calcular similitud semantica: {exc}",
            "centroid_similarity": 0.0,
            "average_similarity": 0.0,
            "token_overlap_ratio": token_overlap_ratio,
            "shared_tokens": sorted(shared_tokens),
            "cluster_categories": sorted(cluster_categories),
            "question_categories": sorted(question_categories),
        }

    centroid_similarity = round(float(centroid_similarity), 4)
    average_similarity = round(float(average_similarity), 4)
    alignment_payload = {
        "centroid_similarity": centroid_similarity,
        "average_similarity": average_similarity,
        "token_overlap_ratio": token_overlap_ratio,
        "shared_tokens": sorted(shared_tokens),
        "cluster_categories": sorted(cluster_categories),
        "question_categories": sorted(question_categories),
        "introduced_primary_categories": sorted(introduced_primary_categories),
    }

    if introduced_primary_categories and (
        "shipping" in introduced_primary_categories or token_overlap_ratio < 0.5
    ):
        return {
            **alignment_payload,
            "status": "misaligned",
            "reason": "La pregunta canonica introduce una intencion primaria que no aparece en el cluster.",
        }
    if category_mismatch and token_overlap_ratio < 0.25:
        return {
            **alignment_payload,
            "status": "misaligned",
            "reason": "La pregunta canonica introduce una categoria distinta a la intencion dominante del cluster.",
        }
    if token_overlap_ratio >= 0.35 and not category_mismatch:
        return {
            **alignment_payload,
            "status": "strong_alignment",
            "reason": "La pregunta comparte terminos centrales con las preguntas del cluster.",
        }
    if (
        max(centroid_similarity, average_similarity) >= FAQ_ALIGNMENT_STRONG_SIMILARITY
        and (category_overlap or token_overlap_ratio >= 0.2 or not cluster_categories)
        and not category_mismatch
    ):
        return {
            **alignment_payload,
            "status": "strong_alignment",
            "reason": "La pregunta es semanticamente cercana al centro del cluster.",
        }
    if (
        max(centroid_similarity, average_similarity) >= FAQ_ALIGNMENT_PARTIAL_SIMILARITY
        or category_overlap
        or token_overlap_ratio > 0
    ) and not (category_mismatch and token_overlap_ratio == 0):
        return {
            **alignment_payload,
            "status": "partial_alignment",
            "reason": "La pregunta tiene alineacion parcial y requiere revision humana.",
        }
    return {
        **alignment_payload,
        "status": "misaligned",
        "reason": "La pregunta canonica esta lejos semanticamente de las preguntas reales del cluster.",
    }


def calculate_cluster_score(
    recurrence: int,
    cluster_cohesion: float,
    generation_confidence: float,
    valid_question_count: int,
    valid_answer_count: int,
) -> float:
    recurrence_score = min(1.0, recurrence / 10)
    evidence_score = min(1.0, (valid_question_count + valid_answer_count) / max(1, recurrence * 2))
    score = (
        0.30 * cluster_cohesion
        + 0.40 * generation_confidence
        + 0.15 * recurrence_score
        + 0.15 * evidence_score
    )
    return round(max(0.0, min(100.0, score * 100)), 2)


QUALITY_METRIC_KEYS = (
    "raw_messages_found",
    "raw_messages_processed",
    "conversations_found",
    "conversations_processed",
    "truncation_applied",
    "truncation_ratio",
    "user_messages_considered",
    "assistant_messages_considered",
    "user_assistant_pairs_built",
    "pairs_rejected_no_assistant_followup",
    "pairs_rejected_empty_text",
    "candidate_questions_detected",
    "candidate_questions_rejected_initial_filter",
    "candidate_questions_kept_for_embedding",
    "embeddings_generated",
    "clusters_detected_before_quality_gates",
    "clusters_rejected_low_size",
    "clusters_rejected_low_support",
    "clusters_rejected_low_cohesion",
    "clusters_rejected_mixed_intent",
    "rejected_low_support",
    "rejected_low_cohesion",
    "rejected_mixed_intent",
    "clusters_rejected_duplicate_existing_faq",
    "rejected_insufficient_question_evidence",
    "rejected_insufficient_answer_evidence",
    "llm_calls_attempted",
    "llm_parse_failures",
    "llm_publish_false",
    "rejected_generation_confidence",
    "rejected_invalid_knowledge_statement",
    "rejected_invalid_canonical_question",
    "rejected_invalid_canonical_answer",
    "rejected_question_too_generic",
    "rejected_question_answer_misaligned",
    "rejected_answer_not_supported_by_evidence",
    "rejected_llm_output_contaminated",
    "rejected_validator_overstrict_condition",
    "duplicates_against_existing_faqs",
    "duplicates_against_previous_candidates",
    "candidates_skipped_existing",
    "candidates_superseded",
    "accepted_candidates",
    "accepted_candidates_total",
    "accepted_high_confidence_candidates",
    "accepted_needs_review_candidates",
    "hard_rejected",
    "persisted_high_confidence",
    "persisted_needs_review",
    "persisted_question_with_answer_review",
    "repair_attempts",
    "repair_successes",
    "repair_failed_but_persisted_for_review",
    "repair_failed_and_rejected",
    "cluster_question_alignment_strong",
    "cluster_question_alignment_partial",
    "cluster_question_alignment_failed",
    "repair_alignment_attempts",
    "repair_alignment_successes",
    "support_examples_deduplicated",
    "candidates_rejected_due_to_cluster_misalignment",
    "candidates_recovered_after_alignment_repair",
)


def empty_quality_metrics() -> Dict[str, int]:
    return {key: 0 for key in QUALITY_METRIC_KEYS}


def build_company_candidates(
    conversations: List[Dict[str, Any]],
    existing_questions: Optional[List[str]] = None,
    previous_candidate_questions: Optional[List[str]] = None,
    run_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    existing_questions = existing_questions or []
    previous_candidate_questions = previous_candidate_questions or []
    valid_items = []
    rejected_initial_filter = 0
    for item in conversations:
        raw_user_text = normalize_text(item.get("user_text"))
        user_text = normalize_chat_text(raw_user_text, for_embedding=True)

        if user_text and is_good_faq_candidate(user_text):
            valid_items.append(
                {
                    **item,
                    "original_user_text": raw_user_text,
                    "user_text": user_text,
                }
            )
        elif raw_user_text:
            rejected_initial_filter += 1

    stats = {
        "valid_examples": len(valid_items),
        "duplicates_omitted": 0,
        "clusters_valid": 0,
        "clusters_rejected": 0,
        "llm_rejected": 0,
        "validator_rejected": 0,
        "silhouette_score": None,
        "candidate_questions_detected": sum(1 for item in conversations if normalize_text(item.get("user_text"))),
        "candidate_questions_rejected_initial_filter": rejected_initial_filter,
        "candidate_questions_kept_for_embedding": len(valid_items),
        "embeddings_generated": 0,
        **empty_quality_metrics(),
    }
    stats["candidate_questions_detected"] = sum(1 for item in conversations if normalize_text(item.get("user_text")))
    stats["candidate_questions_rejected_initial_filter"] = rejected_initial_filter
    stats["candidate_questions_kept_for_embedding"] = len(valid_items)
    minimum_items_for_clustering = 2 if FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS else FAQ_MIN_CLUSTER_SIZE
    if len(valid_items) < minimum_items_for_clustering:
        return [], stats

    user_texts = [item["user_text"] for item in valid_items]
    embeddings = encode_texts(user_texts)
    stats["embeddings_generated"] = len(embeddings)
    labels = cluster_embeddings(embeddings)
    stats["silhouette_score"] = compute_silhouette(embeddings, labels)

    cluster_groups: Dict[int, List[int]] = defaultdict(list)
    for index, label in enumerate(labels):
        if label != -1:
            cluster_groups[int(label)].append(index)
    stats["clusters_detected_before_quality_gates"] = len(cluster_groups)

    candidates: List[Dict[str, Any]] = []
    for label, indices in cluster_groups.items():
        review_reasons: List[str] = []
        is_two_example_cluster = len(indices) == 2
        if len(indices) < FAQ_MIN_CLUSTER_SIZE:
            if not (FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS and is_two_example_cluster):
                stats["clusters_rejected"] += 1
                stats["clusters_rejected_low_size"] += 1
                stats["hard_rejected"] += 1
                continue
            review_reasons.append("cluster de 2 ejemplos requiere revision humana")
        center = _normalize_vector(_mean_vector([embeddings[index] for index in indices]))
        support_values = [_dot(embeddings[index], center) for index in indices]
        support = round(sum(support_values) / len(support_values), 4)
        min_support = round(min(support_values), 4)
        cluster_cohesion = round((support + min_support) / 2, 4)
        if is_two_example_cluster and cluster_cohesion < FAQ_TWO_EXAMPLE_MIN_COHESION:
            stats["clusters_rejected"] += 1
            stats["clusters_rejected_low_cohesion"] += 1
            stats["rejected_low_cohesion"] += 1
            stats["hard_rejected"] += 1
            print(
                "Cluster de 2 descartado por cohesion insuficiente: "
                f"support={support}, min_support={min_support}, cohesion={cluster_cohesion}"
            )
            continue
        if support < FAQ_REJECT_CLUSTER_SUPPORT:
            stats["clusters_rejected"] += 1
            stats["clusters_rejected_low_support"] += 1
            stats["rejected_low_support"] += 1
            stats["hard_rejected"] += 1
            print(
                "Cluster descartado por bajo soporte semantico: "
                f"support={support}, min_support={min_support}, cohesion={cluster_cohesion}"
            )
            continue
        if cluster_cohesion < FAQ_REJECT_CLUSTER_COHESION:
            if cluster_cohesion < 0.28:
                stats["clusters_rejected"] += 1
                stats["clusters_rejected_low_cohesion"] += 1
                stats["rejected_low_cohesion"] += 1
                stats["hard_rejected"] += 1
                print(
                    "Cluster descartado por cohesion extremadamente baja: "
                    f"support={support}, min_support={min_support}, cohesion={cluster_cohesion}"
                )
                continue

            review_reasons.append("cohesion baja: requiere revision humana")
        if support < FAQ_MIN_CLUSTER_SUPPORT:
            review_reasons.append("soporte semantico medio")
        if cluster_cohesion < FAQ_MIN_CLUSTER_COHESION:
            review_reasons.append("cohesion media")

        best_index = max(indices, key=lambda idx: _dot(embeddings[idx], center))
        representative = valid_items[best_index]
        cluster_questions = [user_texts[index] for index in indices]
        intent_categories = cluster_intent_categories(cluster_questions)
        mixed_cluster, mixed_reason = is_mixed_intent_cluster(cluster_questions)
        if mixed_cluster:
            if len(indices) > 12:
                stats["clusters_rejected"] += 1
                stats["clusters_rejected_mixed_intent"] += 1
                stats["rejected_mixed_intent"] += 1
                stats["hard_rejected"] += 1
                print(f"Cluster descartado por intenciones mezcladas: {mixed_reason}")
                continue

            review_reasons.append("cluster con posibles intenciones mezcladas: requiere revision humana")

        examples: List[str] = []
        example_records: List[Dict[str, Any]] = []
        sorted_indices = sorted(indices, key=lambda item_index: _dot(embeddings[item_index], center), reverse=True)
        for idx in sorted_indices:
            example_text = clean_question_evidence(user_texts[idx])
            if example_text not in examples:
                examples.append(example_text)
            example_records.append(
                {
                    "message_pk": valid_items[idx].get("user_message_pk"),
                    "conversation_pk": valid_items[idx].get("conversation_pk"),
                    "original_user_message": example_text,
                    "assistant_response": clean_answer_evidence(
                        valid_items[idx].get("assistant_text", ""),
                        representative.get("company_name"),
                    ),
                    "similarity_score": round(float(_dot(embeddings[idx], center)), 6),
                    "embedding": embeddings[idx],
                }
            )

        answers = [normalize_text(valid_items[index].get("assistant_text")) for index in indices]
        clean_answer_count = sum(1 for answer in answers if is_answer_evidence_usable(clean_answer_evidence(answer, representative.get("company_name"))))
        if len(examples) < FAQ_MIN_QUESTION_EVIDENCE and not (len(examples) == 1 and len(indices) >= FAQ_MIN_CLUSTER_SIZE):
            stats["clusters_rejected"] += 1
            stats["rejected_insufficient_question_evidence"] += 1
            stats["hard_rejected"] += 1
            print(f"Cluster descartado por evidencia insuficiente de pregunta: ejemplos={len(examples)}")
            continue
        if len(examples) == 1 and len(indices) >= FAQ_MIN_CLUSTER_SIZE:
            review_reasons.append("un solo ejemplo unico repetido requiere revision humana")
        if clean_answer_count < 1 and not intent_categories:
            stats["clusters_rejected"] += 1
            stats["rejected_insufficient_answer_evidence"] += 1
            stats["hard_rejected"] += 1
            print("Cluster descartado por falta de evidencia de respuesta e intencion poco clara.")
            continue
        if clean_answer_count < 1:
            review_reasons.append("poca evidencia historica limpia de respuesta")

        cluster_metrics = {
            "cluster_label": label,
            "cluster_support": support,
            "min_support": min_support,
            "cluster_cohesion": cluster_cohesion,
            "valid_question_evidence_count": len(examples),
            "valid_answer_evidence_count": clean_answer_count,
        }
        stats["llm_calls_attempted"] += 1
        generation = generate_faq_candidate_with_llm(
            company_name=representative.get("company_name"),
            questions=cluster_questions,
            historical_answers=answers,
            cluster_metrics=cluster_metrics,
            recurrence=len(indices),
        )
        if generation.get("reason") == "Qwen devolvio JSON invalido.":
            stats["llm_parse_failures"] += 1
        support_result = is_generation_supported_by_evidence(generation, cluster_questions, answers)
        support_level = support_result["support_level"]
        requires_answer_review = bool(support_result["requires_answer_review"])
        candidate_kind = "question_with_answer_review" if requires_answer_review else "full_faq"
        review_reason_code = None
        repaired = bool(generation.get("repaired", False))
        if support_result["should_hard_reject"]:
            recovered_generation = recover_unsupported_generation_for_review(
                generation,
                cluster_questions,
                intent_categories,
                question_embeddings=[embeddings[index] for index in indices],
                cluster_center=center,
            )
            if recovered_generation:
                generation = recovered_generation
                support_result = {
                    "support_level": "weak",
                    "reason": "pregunta detectada con respuesta a revisar",
                    "should_hard_reject": False,
                    "requires_answer_review": True,
                    "overlap_ratio": 0.0,
                }
                support_level = "weak"
                requires_answer_review = True
                candidate_kind = "question_with_answer_review"
                review_reasons.append("respuesta no sustentada reemplazada por formulacion prudente")
                review_reason_code = "answer_unsupported_recovered_for_review"
            else:
                support_rejection_reason = support_result["reason"]
                stats["clusters_rejected"] += 1
                stats["validator_rejected"] += 1
                stats["hard_rejected"] += 1
                stats[support_rejection_reason] += 1
                print(
                    "Cluster descartado por falta de soporte en evidencia: "
                    f"{support_rejection_reason}; question={generation.get('canonical_question')}; "
                    f"answer={generation.get('canonical_answer')}"
                )
                continue
        if support_result["should_hard_reject"]:
            support_rejection_reason = support_result["reason"]
            stats["clusters_rejected"] += 1
            stats["validator_rejected"] += 1
            stats["hard_rejected"] += 1
            stats[support_rejection_reason] += 1
            print(
                "Cluster descartado por falta de soporte en evidencia: "
                f"{support_rejection_reason}; question={generation.get('canonical_question')}; "
                f"answer={generation.get('canonical_answer')}"
            )
            continue
        if requires_answer_review:
            review_reasons.append(support_result["reason"])
            review_reason_code = "answer_partial_support"

        is_valid_generation, rejection_reason = validate_generated_candidate(generation)
        if not is_valid_generation:
            repaired_answer = build_prudent_answer(cluster_questions, intent_categories)
            if (
                rejection_reason == "rejected_invalid_canonical_answer"
                and generation.get("publish")
                and is_valid_canonical_question(generation.get("canonical_question", ""))
                and repaired_answer
                and is_valid_canonical_answer(repaired_answer)
            ):
                generation["canonical_answer"] = repaired_answer
                generation["knowledge_statement"] = repaired_answer
                generation["confidence"] = min(float(generation.get("confidence") or 0.0), 0.62)
                generation["reason"] = normalize_text(
                    f"{generation.get('reason', '')} Respuesta reemplazada por formulacion prudente sin inventar detalles."
                )
                review_reasons.append("respuesta prudente generada tras limpiar salida del LLM")
                review_reason_code = review_reason_code or "prudent_answer_repair"
                requires_answer_review = True
                candidate_kind = "question_with_answer_review"
                is_valid_generation, rejection_reason = validate_generated_candidate(generation)
            if (
                rejection_reason == "rejected_question_answer_misaligned"
                and generation.get("publish")
                and is_valid_canonical_question(generation.get("canonical_question", ""))
                and repaired_answer
                and is_valid_canonical_answer(repaired_answer)
            ):
                generation["canonical_answer"] = repaired_answer
                generation["knowledge_statement"] = repaired_answer
                generation["confidence"] = min(float(generation.get("confidence") or 0.0), 0.62)
                generation["reason"] = normalize_text(
                    f"{generation.get('reason', '')} Respuesta reemplazada por formulacion prudente por desalineacion pregunta/respuesta."
                )
                review_reasons.append("respuesta prudente generada por desalineacion pregunta/respuesta")
                review_reason_code = review_reason_code or "question_answer_misaligned_recovered_for_review"
                requires_answer_review = True
                candidate_kind = "question_with_answer_review"
                is_valid_generation, rejection_reason = validate_generated_candidate(generation)
            if not is_valid_generation and rejection_reason in {
                "rejected_invalid_canonical_question",
                "rejected_invalid_knowledge_statement",
                "rejected_question_answer_misaligned",
            } and generation.get("publish"):
                stats["repair_attempts"] += 1
                repaired_generation = repair_faq_candidate_with_llm(
                    company_name=representative.get("company_name"),
                    generation=generation,
                    rejection_reason=rejection_reason,
                    questions=cluster_questions,
                    historical_answers=answers,
                    cluster_metrics=cluster_metrics,
                    cluster_intent_statement=generation.get("cluster_intent_statement"),
                )
                if repaired_generation:
                    repaired_support = is_generation_supported_by_evidence(repaired_generation, cluster_questions, answers)
                    repaired_valid, repaired_reason = validate_generated_candidate(repaired_generation)
                    if repaired_valid and not repaired_support["should_hard_reject"]:
                        generation = repaired_generation
                        support_result = repaired_support
                        support_level = repaired_support["support_level"]
                        requires_answer_review = bool(repaired_support["requires_answer_review"])
                        candidate_kind = "question_with_answer_review" if requires_answer_review else "full_faq"
                        review_reasons.append("pregunta/respuesta corregida automaticamente")
                        review_reason_code = "auto_repaired"
                        repaired = True
                        stats["repair_successes"] += 1
                        is_valid_generation = True
                        rejection_reason = ""
                    else:
                        generation.setdefault("repair_rejection_reason", repaired_reason)
                if not is_valid_generation:
                    can_persist, repair_review_reason, repair_review_code = can_persist_generation_for_review(
                        generation,
                        rejection_reason,
                        intent_categories,
                    )
                    if can_persist:
                        review_reasons.append(repair_review_reason or "requiere revision humana")
                        review_reason_code = repair_review_code or review_reason_code
                        requires_answer_review = True
                        candidate_kind = "question_with_answer_review"
                        stats["repair_failed_but_persisted_for_review"] += 1
                        is_valid_generation = True
                    else:
                        stats["repair_failed_and_rejected"] += 1
            if not is_valid_generation:
                stats["clusters_rejected"] += 1
                stats["hard_rejected"] += 1
                if generation.get("publish") is False:
                    stats["llm_rejected"] += 1
                    stats["llm_publish_false"] += 1
                else:
                    stats["validator_rejected"] += 1
                    if rejection_reason in stats:
                        stats[rejection_reason] += 1
                print(
                    "Cluster descartado despues de generacion: "
                    f"{rejection_reason}; confidence={generation.get('confidence')}; "
                    f"question={generation.get('canonical_question')}; reason={generation.get('reason')}"
                )
                continue

        alignment_result = is_canonical_question_aligned_with_cluster(
            generation.get("canonical_question", ""),
            cluster_questions,
            question_embeddings=[embeddings[index] for index in indices],
            cluster_center=center,
        )
        alignment_status = alignment_result["status"]
        if alignment_status == "strong_alignment":
            stats["cluster_question_alignment_strong"] += 1
        elif alignment_status == "partial_alignment":
            stats["cluster_question_alignment_partial"] += 1
            review_reasons.append("alineacion parcial entre pregunta FAQ y cluster")
            review_reason_code = review_reason_code or "cluster_question_partial_alignment"
        else:
            stats["cluster_question_alignment_failed"] += 1
            stats["repair_alignment_attempts"] += 1
            stats["repair_attempts"] += 1
            repaired_generation = repair_faq_candidate_with_llm(
                company_name=representative.get("company_name"),
                generation=generation,
                rejection_reason="rejected_cluster_misalignment",
                questions=cluster_questions,
                historical_answers=answers,
                cluster_metrics=cluster_metrics,
                cluster_intent_statement=generation.get("cluster_intent_statement"),
                alignment_reason=alignment_result.get("reason"),
            )
            repaired_accepted = False
            if repaired_generation:
                repaired_support = is_generation_supported_by_evidence(repaired_generation, cluster_questions, answers)
                repaired_valid, repaired_reason = validate_generated_candidate(repaired_generation)
                repaired_alignment = is_canonical_question_aligned_with_cluster(
                    repaired_generation.get("canonical_question", ""),
                    cluster_questions,
                    question_embeddings=[embeddings[index] for index in indices],
                    cluster_center=center,
                )
                if repaired_valid and not repaired_support["should_hard_reject"] and repaired_alignment["status"] != "misaligned":
                    generation = repaired_generation
                    support_result = repaired_support
                    support_level = repaired_support["support_level"]
                    requires_answer_review = bool(repaired_support["requires_answer_review"])
                    candidate_kind = "question_with_answer_review" if requires_answer_review else "full_faq"
                    alignment_result = repaired_alignment
                    alignment_status = repaired_alignment["status"]
                    review_reasons.append("pregunta FAQ reparada para alinearse al cluster")
                    review_reason_code = "auto_repaired_cluster_alignment"
                    repaired = True
                    repaired_accepted = True
                    stats["repair_successes"] += 1
                    stats["repair_alignment_successes"] += 1
                    stats["candidates_recovered_after_alignment_repair"] += 1
                    if repaired_alignment["status"] == "partial_alignment":
                        review_reasons.append("alineacion parcial entre pregunta reparada y cluster")
                        requires_answer_review = True
                        candidate_kind = "question_with_answer_review"
                else:
                    generation.setdefault("repair_rejection_reason", repaired_reason)
            if not repaired_accepted:
                stats["clusters_rejected"] += 1
                stats["validator_rejected"] += 1
                stats["hard_rejected"] += 1
                stats["candidates_rejected_due_to_cluster_misalignment"] += 1
                print(
                    "Cluster descartado por desalineacion pregunta-cluster: "
                    f"{alignment_result.get('reason')}; question={generation.get('canonical_question')}; "
                    f"cluster_intent={generation.get('cluster_intent_statement')}"
                )
                continue

        question_text = generation["canonical_question"]
        answer_text = generation["canonical_answer"]
        knowledge_statement = generation.get("knowledge_statement")
        if knowledge_statement == "":
            knowledge_statement = answer_text
        cluster_intent_statement = generation.get("cluster_intent_statement")
        if not is_valid_cluster_intent_statement(cluster_intent_statement or ""):
            cluster_intent_statement = derive_cluster_intent_statement(cluster_questions)
        support_examples, deduplicated_count = select_support_examples_for_candidate(
            question_text,
            example_records,
            limit=3,
        )
        stats["support_examples_deduplicated"] += deduplicated_count

        if FAQ_SKIP_EXISTING and is_existing_faq(question_text, existing_questions):
            stats["clusters_rejected"] += 1
            stats["duplicates_omitted"] += 1
            stats["duplicates_against_existing_faqs"] += 1
            stats["clusters_rejected_duplicate_existing_faq"] += 1
            stats["candidates_skipped_existing"] += 1
            print(f"Candidato omitido por duplicado contra FAQ existente: {question_text}")
            continue

        previous_norms = {normalize_question(question) for question in previous_candidate_questions}
        current_norm = normalize_question(question_text)
        previous_candidate_exact_duplicate = current_norm in previous_norms
        previous_candidate_semantic_duplicate = False
        if previous_candidate_exact_duplicate:
            stats["duplicates_against_previous_candidates"] += 1
            stats["candidates_superseded"] += 1
        elif FAQ_SKIP_EXISTING and is_existing_faq(question_text, previous_candidate_questions):
            previous_candidate_semantic_duplicate = True
            stats["duplicates_against_previous_candidates"] += 1
            stats["candidates_superseded"] += 1
            stats["candidates_skipped_existing"] += 1
            print(f"Candidato omitido por similitud con candidato previo validado/rechazado: {question_text}")
            continue

        created_dates = [
            parsed_date
            for index in indices
            if (parsed_date := parse_datetime(valid_items[index].get("created_at"))) is not None
        ]

        first_seen = min(created_dates) if created_dates else None
        last_seen = max(created_dates) if created_dates else None
        workspace_id = representative.get("workspace_id")
        cluster_score = calculate_cluster_score(
            recurrence=len(indices),
            cluster_cohesion=cluster_cohesion,
            generation_confidence=float(generation["confidence"]),
            valid_question_count=len(examples),
            valid_answer_count=clean_answer_count,
        )
        quality_tier, review_reason = classify_quality_tier(
            generation,
            cluster_cohesion=cluster_cohesion,
            support=support,
            valid_answer_count=clean_answer_count,
            review_reasons=review_reasons,
        )
        candidate_status = "needs_review" if quality_tier == "needs_review" else "pending"

        candidates.append(
            {
                "candidate_id": str(uuid.uuid4()),
                "run_id": run_id,
                "workspace_id": int(workspace_id) if workspace_id is not None else None,
                "company_id": company_key(representative),
                "company_name": representative.get("company_name"),
                "agent_id": _most_common_agent_id(valid_items[index] for index in indices),
                "normalized_question": question_text,
                "suggested_answer": answer_text,
                "cluster_label": question_text[:120],
                "recurrence_count": len(indices),
                "first_seen_at": first_seen,
                "last_seen_at": last_seen,
                "cluster_id": None,
                "algorithm": "DBSCAN",
                "centroid_embedding": center,
                "cluster_size": len(indices),
                "support_examples": support_examples,
                "cluster_score": cluster_score,
                "status": candidate_status,
                "cluster_metadata": {
                    "label": label,
                    "support": support,
                    "min_support": min_support,
                    "cluster_cohesion": cluster_cohesion,
                    "eps": FAQ_CLUSTER_EPS,
                    "min_cluster_size": FAQ_MIN_CLUSTER_SIZE,
                    "allow_two_example_clusters": FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS,
                    "two_example_min_cohesion": FAQ_TWO_EXAMPLE_MIN_COHESION,
                    "embedding_backend": FAQ_EMBEDDING_BACKEND,
                    "embedding_model": current_embedding_model_label(),
                    "embedding_model_configured": MODEL_NAME,
                    "intent_categories": sorted(intent_categories),
                },
                "candidate_metadata": {
                    "cluster_score": cluster_score,
                    "support": support,
                    "cluster_cohesion": cluster_cohesion,
                    "quality_tier": quality_tier,
                    "review_reason": review_reason,
                    "review_reason_code": review_reason_code,
                    "generation_confidence": generation["confidence"],
                    "generation_reason": generation["reason"],
                    "cluster_intent_statement": cluster_intent_statement,
                    "knowledge_statement": knowledge_statement,
                    "cluster_question_alignment": alignment_status,
                    "cluster_question_alignment_details": alignment_result,
                    "candidate_kind": candidate_kind,
                    "requires_answer_review": requires_answer_review,
                    "support_level": support_level,
                    "evidence_support": support_result,
                    "repaired": repaired,
                    "valid_answer_evidence_count": clean_answer_count,
                    "valid_question_evidence_count": len(examples),
                    "duplicate_threshold": FAQ_DUPLICATE_THRESHOLD,
                    "llm_model": FAQ_LLM_MODEL if ANSWER_GENERATOR else "unavailable",
                    "generation_mode": generation.get("mode", "unknown"),
                    "confidence_was_repaired": bool(generation.get("confidence_was_repaired", False)),
                    "deduplication_status": "superseded_previous_candidate"
                    if previous_candidate_exact_duplicate
                    else "similar_to_previous_candidate"
                    if previous_candidate_semantic_duplicate
                    else "new_candidate",
                    "embedding_backend": FAQ_EMBEDDING_BACKEND,
                    "embedding_model": current_embedding_model_label(),
                    "embedding_model_configured": MODEL_NAME,
                    "rejected_by_validator": None,
                    "run_id": run_id,
                    "is_two_example_cluster": is_two_example_cluster,
                },
                "examples": example_records[:10],
            }
        )
        stats["clusters_valid"] += 1
        stats["accepted_candidates"] += 1
        stats["accepted_candidates_total"] += 1
        if quality_tier == "needs_review":
            stats["accepted_needs_review_candidates"] += 1
            stats["persisted_needs_review"] += 1
        else:
            stats["accepted_high_confidence_candidates"] += 1
            stats["persisted_high_confidence"] += 1
        if candidate_kind == "question_with_answer_review":
            stats["persisted_question_with_answer_review"] += 1

    return candidates, stats


def build_suggestion_candidates(
    conversations: List[Dict[str, Any]],
    existing_faqs_by_company: Optional[Dict[str, List[str]]] = None,
    previous_candidates_by_company: Optional[Dict[str, List[str]]] = None,
    run_id: Optional[str] = None,
    ingest_metrics: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    conversations_by_company: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in conversations:
        conversations_by_company[company_key(item)].append(item)

    existing_faqs_by_company = existing_faqs_by_company or load_existing_faqs_by_company()
    previous_candidates_by_company = previous_candidates_by_company or load_previous_candidates_by_company()
    all_candidates: List[Dict[str, Any]] = []
    silhouettes: List[float] = []
    total_valid = 0
    duplicates = 0
    valid_clusters = 0
    rejected_clusters = 0
    llm_rejected = 0
    validator_rejected = 0
    quality_totals = empty_quality_metrics()

    for company_id, items in conversations_by_company.items():
        candidates, stats = build_company_candidates(
            items,
            existing_questions=existing_faqs_by_company.get(company_id, []),
            previous_candidate_questions=previous_candidates_by_company.get(company_id, []),
            run_id=run_id,
        )
        all_candidates.extend(candidates)
        total_valid += int(stats["valid_examples"])
        duplicates += int(stats["duplicates_omitted"])
        valid_clusters += int(stats["clusters_valid"])
        rejected_clusters += int(stats["clusters_rejected"])
        llm_rejected += int(stats["llm_rejected"])
        validator_rejected += int(stats["validator_rejected"])
        for key in QUALITY_METRIC_KEYS:
            quality_totals[key] += int(stats.get(key, 0))
        if stats["silhouette_score"] is not None:
            silhouettes.append(float(stats["silhouette_score"]))

    silhouette = round(sum(silhouettes) / len(silhouettes), 4) if silhouettes else None
    stats = {
        "company_count": len(conversations_by_company),
        "conversations_analyzed": len(conversations),
        "total_examples": total_valid,
        "clusters_valid": valid_clusters,
        "clusters_rejected": rejected_clusters,
        "llm_rejected": llm_rejected,
        "validator_rejected": validator_rejected,
        "duplicates_omitted": duplicates,
        "silhouette_score": silhouette,
        "average_cluster_size": round(total_valid / valid_clusters, 2) if valid_clusters else 0.0,
        **quality_totals,
    }
    for key, value in (ingest_metrics or {}).items():
        if isinstance(value, bool):
            stats[key] = int(value)
        elif isinstance(value, (int, float)):
            stats[key] = value
        else:
            stats[key] = value
    return all_candidates, stats


def _candidate_to_response(candidate: Dict[str, Any]) -> SuggestionResponse:
    metadata = candidate.get("candidate_metadata") or {}
    support_examples, _deduplicated = deduplicate_support_example_texts(candidate.get("support_examples") or [], limit=3)
    return SuggestionResponse(
        id=str(candidate["candidate_id"]),
        company_id=str(candidate.get("company_id") or candidate.get("workspace_id")),
        company_name=candidate.get("company_name"),
        workspace_id=int(candidate["workspace_id"]),
        agent_id=str(candidate["agent_id"]) if candidate.get("agent_id") else None,
        question=candidate["normalized_question"],
        answer=candidate.get("suggested_answer") or "",
        cluster_size=int(candidate.get("recurrence_count") or candidate.get("cluster_size") or 0),
        support_examples=support_examples,
        cluster_score=float(candidate.get("cluster_score") or 0.0),
        quality_tier=metadata.get("quality_tier"),
        review_reason=metadata.get("review_reason"),
        review_reason_code=metadata.get("review_reason_code"),
        generation_confidence=metadata.get("generation_confidence"),
        cluster_intent_statement=metadata.get("cluster_intent_statement"),
        knowledge_statement=metadata.get("knowledge_statement"),
        cluster_question_alignment=metadata.get("cluster_question_alignment"),
        candidate_kind=metadata.get("candidate_kind") or "full_faq",
        requires_answer_review=bool(metadata.get("requires_answer_review") or False),
        support_level=metadata.get("support_level"),
        repaired=bool(metadata.get("repaired") or False),
        since_days_used=metadata.get("since_days"),
        was_human_edited=bool(candidate.get("was_human_edited") or False),
        last_edited_by=candidate.get("last_edited_by"),
        last_edited_at=candidate.get("last_edited_at"),
        edit_count=int(candidate.get("edit_count") or 0),
        status=candidate.get("status", "pending"),
        created_at=candidate.get("created_at"),
        updated_at=candidate.get("updated_at"),
        run_id=candidate.get("run_id"),
    )


SUMMARY_METRIC_FIELDS = (
    "limit",
    "effective_limit",
    "analysis_limit_mode",
    "raw_messages_found",
    "raw_messages_processed",
    "total_messages_found_before_limit",
    "total_messages_processed_after_limit",
    "conversations_found",
    "conversations_processed",
    "conversations_found_before_limit",
    "conversations_processed_after_limit",
    "truncation_applied",
    "truncation_ratio",
    "hard_rejected",
    "persisted_high_confidence",
    "persisted_needs_review",
    "persisted_question_with_answer_review",
    "cluster_question_alignment_strong",
    "cluster_question_alignment_partial",
    "cluster_question_alignment_failed",
    "repair_alignment_attempts",
    "repair_alignment_successes",
    "support_examples_deduplicated",
    "candidates_rejected_due_to_cluster_misalignment",
    "candidates_recovered_after_alignment_repair",
)


def summary_metric_payload(stats: Dict[str, Any]) -> Dict[str, Any]:
    return {field: stats.get(field) for field in SUMMARY_METRIC_FIELDS if field in stats}


def build_suggestions(conversations: List[Dict[str, Any]]) -> SuggestionSummary:
    candidates, stats = build_suggestion_candidates(conversations)
    suggestions = [_candidate_to_response(candidate) for candidate in candidates]
    summary = SuggestionSummary(
        company_count=stats["company_count"],
        cluster_count=len(suggestions),
        total_examples=stats["total_examples"],
        average_cluster_size=stats["average_cluster_size"],
        silhouette_score=stats["silhouette_score"],
        suggestions=suggestions,
        **summary_metric_payload(stats),
    )
    save_json(summary.model_dump(), SUGGESTIONS_PATH)
    return summary


def create_pipeline_run(parameters: Dict[str, Any], workspace_id: Optional[int] = None) -> str:
    query = f"""
        INSERT INTO {FAQ_SCHEMA}.pipeline_runs (workspace_id, status, parameters, notes)
        VALUES (%s, 'running', %s, %s)
        RETURNING run_id::text
    """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    workspace_id,
                    Json(parameters),
                    "Pipeline manual/API iniciado.",
                ),
            )

            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("No se pudo crear pipeline_run: PostgreSQL no devolvió run_id.")

            return str(row[0])


def finish_pipeline_run(run_id: str, status: str, notes: Optional[str] = None) -> None:
    query = f"""
        UPDATE {FAQ_SCHEMA}.pipeline_runs
        SET status = %s, finished_at = now(), notes = COALESCE(%s, notes)
        WHERE run_id = %s
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (status, notes, run_id))


def insert_metric(cursor: Any, run_id: str, name: str, value: Optional[float], metadata: Optional[Dict[str, Any]] = None) -> None:
    cursor.execute(
        f"""
        INSERT INTO {FAQ_SCHEMA}.pipeline_metrics (run_id, metric_name, metric_value, metric_metadata)
        VALUES (%s, %s, %s, %s)
        """,
        (run_id, name, value, Json(metadata or {})),
    )


def persist_embedding(cursor: Any, candidate: Dict[str, Any], example: Dict[str, Any]) -> int:
    message_pk = example.get("message_pk")
    embedding = example.get("embedding")
    if not message_pk or not embedding:
        return 0

    cursor.execute(
        f"""
        INSERT INTO {FAQ_SCHEMA}.message_embeddings (
            message_pk, workspace_id, agent_id, embedding_model, embedding_dimension, embedding, embedding_metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (message_pk, embedding_model) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            agent_id = EXCLUDED.agent_id,
            embedding_dimension = EXCLUDED.embedding_dimension,
            embedding = EXCLUDED.embedding,
            embedding_metadata = EXCLUDED.embedding_metadata
        """,
        (
            message_pk,
            candidate["workspace_id"],
            candidate.get("agent_id"),
            EMBEDDING_BACKEND_READY,
            len(embedding),
            embedding,
            Json({"normalized": True, "source": "faq_suggestion_pipeline"}),
        ),
    )
    return 1


def find_existing_candidate(cursor: Any, candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cursor.execute(
        f"""
        SELECT candidate_id::text, status
        FROM {FAQ_SCHEMA}.faq_candidates
        WHERE workspace_id = %s
          AND COALESCE(agent_id::text, '') = COALESCE(%s, '')
          AND lower(normalized_question) = lower(%s)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (candidate["workspace_id"], candidate.get("agent_id"), candidate["normalized_question"]),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def persist_pipeline_results(run_id: str, candidates: List[Dict[str, Any]], stats: Dict[str, Any]) -> List[SuggestionResponse]:
    responses: List[SuggestionResponse] = []
    embeddings_persisted = 0

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            for candidate in candidates:
                if candidate["workspace_id"] is None:
                    continue

                cursor.execute(
                    f"""
                    INSERT INTO {FAQ_SCHEMA}.question_clusters (
                        run_id, workspace_id, agent_id, cluster_label, algorithm,
                        representative_question, centroid_embedding, cluster_size, cluster_metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING cluster_id::text
                    """,
                    (
                        run_id,
                        candidate["workspace_id"],
                        candidate.get("agent_id"),
                        candidate["cluster_label"],
                        candidate["algorithm"],
                        candidate["normalized_question"],
                        candidate["centroid_embedding"],
                        candidate["cluster_size"],
                        Json(candidate["cluster_metadata"]),
                    ),
                )
                cluster_row = cursor.fetchone()
                if cluster_row is None:
                    raise RuntimeError("No se pudo crear question_cluster: PostgreSQL no devolvió cluster_id.")

                cluster_id = str(cluster_row["cluster_id"])
                candidate["cluster_id"] = cluster_id

                existing = find_existing_candidate(cursor, candidate)
                if existing:
                    candidate_id = existing["candidate_id"]
                    next_status = (
                        "needs_review"
                        if existing["status"] == "pending" and candidate.get("status") == "needs_review"
                        else existing["status"]
                    )
                    cursor.execute(
                        f"""
                        UPDATE {FAQ_SCHEMA}.faq_candidates
                        SET cluster_id = %s,
                            suggested_answer = %s,
                            cluster_label = %s,
                            recurrence_count = %s,
                            first_seen_at = %s,
                            last_seen_at = %s,
                            status = %s,
                            updated_at = now(),
                            candidate_metadata = COALESCE(candidate_metadata, '{{}}'::jsonb) || %s::jsonb
                        WHERE candidate_id = %s
                        """,
                        (
                            cluster_id,
                            candidate["suggested_answer"],
                            candidate["cluster_label"],
                            candidate["recurrence_count"],
                            candidate["first_seen_at"],
                            candidate["last_seen_at"],
                            next_status,
                            Json(candidate["candidate_metadata"]),
                            candidate_id,
                        ),
                    )
                    candidate["candidate_id"] = candidate_id
                    candidate["status"] = next_status
                else:
                    cursor.execute(
                        f"""
                        INSERT INTO {FAQ_SCHEMA}.faq_candidates (
                            candidate_id, workspace_id, agent_id, cluster_id, normalized_question,
                            suggested_answer, cluster_label, recurrence_count, first_seen_at,
                            last_seen_at, status, candidate_metadata
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING candidate_id::text, status
                        """,
                        (
                            candidate["candidate_id"],
                            candidate["workspace_id"],
                            candidate.get("agent_id"),
                            cluster_id,
                            candidate["normalized_question"],
                            candidate["suggested_answer"],
                            candidate["cluster_label"],
                            candidate["recurrence_count"],
                            candidate["first_seen_at"],
                            candidate["last_seen_at"],
                            candidate.get("status", "pending"),
                            Json(candidate["candidate_metadata"]),
                        ),
                    )
                    created = cursor.fetchone()
                    if created is None:
                        raise RuntimeError("No se pudo crear faq_candidate: PostgreSQL no devolvió candidate_id.")

                    candidate["candidate_id"] = str(created["candidate_id"])
                    candidate["status"] = str(created["status"])
                    cursor.execute(
                        f"""
                        INSERT INTO {FAQ_SCHEMA}.faq_validation_events (
                            candidate_id, event_type, previous_status, new_status, reviewer_identifier, notes
                        )
                        VALUES (%s, 'created', NULL, %s, 'pipeline', %s)
                        """,
                        (
                            candidate["candidate_id"],
                            candidate.get("status", "pending"),
                            f"Generado por pipeline_run={run_id}",
                        ),
                    )

                cursor.execute(
                    f"DELETE FROM {FAQ_SCHEMA}.faq_candidate_examples WHERE candidate_id = %s",
                    (candidate["candidate_id"],),
                )
                for example in candidate["examples"]:
                    embeddings_persisted += persist_embedding(cursor, candidate, example)
                    if not example.get("message_pk") or not example.get("conversation_pk"):
                        continue
                    cursor.execute(
                        f"""
                        INSERT INTO {FAQ_SCHEMA}.faq_candidate_examples (
                            candidate_id, message_pk, conversation_pk, original_user_message,
                            assistant_response, similarity_score
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            candidate["candidate_id"],
                            example["message_pk"],
                            example["conversation_pk"],
                            example["original_user_message"],
                            example["assistant_response"],
                            example["similarity_score"],
                        ),
                    )
                responses.append(_candidate_to_response(candidate))

            metric_values = {
                "conversations_analyzed": stats.get("conversations_analyzed"),
                "faq_candidate_messages": stats.get("total_examples"),
                "candidates_generated": len(responses),
                "clusters_valid": stats.get("clusters_valid"),
                "clusters_rejected": stats.get("clusters_rejected"),
                "llm_rejected": stats.get("llm_rejected"),
                "validator_rejected": stats.get("validator_rejected"),
                "average_cluster_size": stats.get("average_cluster_size"),
                "duplicates_omitted": stats.get("duplicates_omitted"),
                "workspaces_processed": stats.get("company_count"),
                "embeddings_persisted": embeddings_persisted,
                "since_days": stats.get("since_days"),
            }
            metric_values.update({key: stats.get(key, 0) for key in QUALITY_METRIC_KEYS})
            if stats.get("silhouette_score") is not None:
                metric_values["silhouette_score"] = stats.get("silhouette_score")
            for name, value in metric_values.items():
                insert_metric(
                    cursor,
                    run_id,
                    name,
                    value,
                    {
                        "embedding_backend": FAQ_EMBEDDING_BACKEND,
                        "embedding_model": current_embedding_model_label(),
                        "embedding_model_configured": MODEL_NAME,
                        "workspace_id": stats.get("workspace_id"),
                        "since_days": stats.get("since_days"),
                    },
                )

    return responses


def list_suggestions_from_db(
    status: Optional[str] = None,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    include_all: bool = False,
) -> SuggestionSummary:
    filters = []
    params: List[Any] = []
    latest_params: List[Any] = []
    latest_workspace_filter = ""
    latest_order = "finished_at DESC NULLS LAST, started_at DESC"
    if workspace_id is not None:
        latest_workspace_filter = "AND (workspace_id = %s OR workspace_id IS NULL)"
        latest_params.append(workspace_id)
    if not include_all:
        filters.append("qc.run_id = (SELECT run_id FROM latest_successful_run)")
    if status:
        filters.append("fc.status = %s")
        params.append(status)
    if workspace_id is not None:
        filters.append("fc.workspace_id = %s")
        params.append(workspace_id)
    if agent_id:
        filters.append("fc.agent_id = %s")
        params.append(agent_id)
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    query = f"""
        WITH latest_successful_run AS (
            SELECT run_id
            FROM {FAQ_SCHEMA}.pipeline_runs
            WHERE status = 'succeeded'
            {latest_workspace_filter}
            ORDER BY {latest_order}
            LIMIT 1
        )
        SELECT
            fc.candidate_id::text AS candidate_id,
            fc.workspace_id,
            w.name AS company_name,
            fc.agent_id::text AS agent_id,
            fc.normalized_question,
            fc.suggested_answer,
            fc.recurrence_count,
            fc.status,
            fc.created_at,
            fc.updated_at,
            COALESCE(fc.was_human_edited, false) AS was_human_edited,
            fc.last_edited_by,
            fc.last_edited_at,
            fc.candidate_metadata,
            qc.run_id::text AS run_id,
            COALESCE(ec.edit_count, 0) AS edit_count,
            COALESCE(
                array_remove(array_agg(fce.original_user_message ORDER BY fce.similarity_score DESC NULLS LAST), NULL),
                ARRAY[]::text[]
            ) AS support_examples
        FROM {FAQ_SCHEMA}.faq_candidates AS fc
        JOIN {FAQ_SCHEMA}.workspaces AS w
          ON w.workspace_id = fc.workspace_id
        LEFT JOIN {FAQ_SCHEMA}.question_clusters AS qc
          ON qc.cluster_id = fc.cluster_id
        LEFT JOIN {FAQ_SCHEMA}.faq_candidate_examples AS fce
          ON fce.candidate_id = fc.candidate_id
        LEFT JOIN (
            SELECT candidate_id, count(*) AS edit_count
            FROM {FAQ_SCHEMA}.faq_candidate_edit_events
            GROUP BY candidate_id
        ) AS ec
          ON ec.candidate_id = fc.candidate_id
        {where_clause}
        GROUP BY
            fc.candidate_id, fc.workspace_id, w.name, fc.agent_id, fc.normalized_question,
            fc.suggested_answer, fc.recurrence_count, fc.status, fc.created_at, fc.updated_at,
            fc.was_human_edited, fc.last_edited_by, fc.last_edited_at,
            fc.candidate_metadata, qc.run_id, ec.edit_count
        ORDER BY fc.created_at DESC
    """

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, tuple(latest_params + params))
            rows = [dict(row) for row in cursor.fetchall()]

    suggestions = []
    for row in rows:
        metadata = row.get("candidate_metadata") or {}
        support_examples, _deduplicated = deduplicate_support_example_texts(row.get("support_examples") or [], limit=3)
        suggestions.append(
            SuggestionResponse(
                id=row["candidate_id"],
                company_id=str(row["workspace_id"]),
                company_name=row.get("company_name"),
                workspace_id=int(row["workspace_id"]),
                agent_id=row.get("agent_id"),
                question=row["normalized_question"],
                answer=row.get("suggested_answer") or "",
                cluster_size=int(row.get("recurrence_count") or 0),
                support_examples=support_examples,
                cluster_score=float(metadata.get("cluster_score") or 0.0),
                quality_tier=metadata.get("quality_tier"),
                review_reason=metadata.get("review_reason"),
                review_reason_code=metadata.get("review_reason_code"),
                generation_confidence=metadata.get("generation_confidence"),
                cluster_intent_statement=metadata.get("cluster_intent_statement"),
                knowledge_statement=metadata.get("knowledge_statement"),
                cluster_question_alignment=metadata.get("cluster_question_alignment"),
                candidate_kind=metadata.get("candidate_kind") or "full_faq",
                requires_answer_review=bool(metadata.get("requires_answer_review") or False),
                support_level=metadata.get("support_level"),
                repaired=bool(metadata.get("repaired") or False),
                since_days_used=metadata.get("since_days"),
                was_human_edited=bool(row.get("was_human_edited")),
                last_edited_by=row.get("last_edited_by"),
                last_edited_at=row.get("last_edited_at"),
                edit_count=int(row.get("edit_count") or 0),
                status=row.get("status", "pending"),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
                run_id=row.get("run_id"),
            )
        )

    company_count = len({item.company_id for item in suggestions})
    total_examples = sum(item.cluster_size for item in suggestions)
    return SuggestionSummary(
        company_count=company_count,
        cluster_count=len(suggestions),
        total_examples=total_examples,
        average_cluster_size=round(total_examples / len(suggestions), 2) if suggestions else 0.0,
        silhouette_score=None,
        suggestions=suggestions,
        run_id=suggestions[0].run_id if suggestions else None,
    )


def list_workspaces_from_db() -> List[WorkspaceResponse]:
    query = f"""
        SELECT DISTINCT w.workspace_id, w.name AS workspace_name
        FROM {FAQ_SCHEMA}.workspaces AS w
        JOIN {FAQ_SCHEMA}.conversations AS c
          ON c.workspace_id = w.workspace_id
        ORDER BY w.name, w.workspace_id
    """
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            rows = [dict(row) for row in cursor.fetchall()]
    return [
        WorkspaceResponse(
            workspace_id=int(row["workspace_id"]),
            workspace_name=row.get("workspace_name") or f"Workspace {row['workspace_id']}",
            company_id=str(row["workspace_id"]),
        )
        for row in rows
    ]


def log_pipeline_quality_summary(stats: Dict[str, Any]) -> None:
    print("Pipeline quality summary:")
    print(f"- embedding model usado: {current_embedding_model_label()} (configurado: {MODEL_NAME})")
    print(f"- LLM model active: {FAQ_LLM_MODEL if FAQ_LLM_ENABLED else 'disabled'}")
    print(f"- Thinking disabled: {FAQ_LLM_THINKING_DISABLED}")
    print(f"- workspace_id: {stats.get('workspace_id')}")
    print(f"- since_days: {stats.get('since_days')}")
    print(f"- mensajes encontrados/procesados: {stats.get('raw_messages_found', 0)}/{stats.get('raw_messages_processed', 0)}")
    print(f"- conversaciones encontradas/procesadas: {stats.get('conversations_found', 0)}/{stats.get('conversations_processed', 0)}")
    print(f"- truncamiento aplicado: {bool(stats.get('truncation_applied'))} ratio={stats.get('truncation_ratio', 0)}")
    print(f"- preguntas candidatas para embedding: {stats.get('candidate_questions_kept_for_embedding', 0)}")
    print(f"- clusters antes de filtros: {stats.get('clusters_detected_before_quality_gates', 0)}")
    print(f"- descartados por bajo soporte: {stats.get('rejected_low_support', 0)}")
    print(f"- descartados por baja cohesion: {stats.get('rejected_low_cohesion', 0)}")
    print(f"- descartados por intencion mezclada: {stats.get('rejected_mixed_intent', 0)}")
    print(f"- descartados por pregunta invalida: {stats.get('rejected_invalid_canonical_question', 0)}")
    print(f"- descartados por respuesta invalida: {stats.get('rejected_invalid_canonical_answer', 0)}")
    print(f"- alignment fuerte pregunta-cluster: {stats.get('cluster_question_alignment_strong', 0)}")
    print(f"- alignment parcial pregunta-cluster: {stats.get('cluster_question_alignment_partial', 0)}")
    print(f"- alignment fallido pregunta-cluster: {stats.get('cluster_question_alignment_failed', 0)}")
    print(f"- repair alignment intentos/exitos: {stats.get('repair_alignment_attempts', 0)}/{stats.get('repair_alignment_successes', 0)}")
    print(f"- ejemplos de soporte deduplicados: {stats.get('support_examples_deduplicated', 0)}")
    print(f"- rechazados por misalignment: {stats.get('candidates_rejected_due_to_cluster_misalignment', 0)}")
    print(f"- recuperados tras repair alignment: {stats.get('candidates_recovered_after_alignment_repair', 0)}")
    print(f"- descartados por publish=false: {stats.get('llm_publish_false', 0)}")
    print(f"- duplicados contra FAQs existentes: {stats.get('duplicates_against_existing_faqs', 0)}")
    print(f"- duplicados contra candidatos previos: {stats.get('duplicates_against_previous_candidates', 0)}")
    print(f"- candidatos high confidence: {stats.get('accepted_high_confidence_candidates', 0)}")
    print(f"- candidatos needs review: {stats.get('accepted_needs_review_candidates', 0)}")
    print(f"- candidatos question_with_answer_review: {stats.get('persisted_question_with_answer_review', 0)}")
    print(f"- hard rejected: {stats.get('hard_rejected', 0)}")
    print(f"- candidatos totales aceptados: {stats.get('accepted_candidates', 0)}")


def run_suggestion_pipeline(request: Optional[IngestRequest] = None) -> SuggestionSummary:
    if request is None:
        request = IngestRequest(
            limit=None,
            since_days=get_int_env("FAQ_DEFAULT_SINCE_DAYS", 90),
            workspace_id=None,
            agent_id=None,
        )
    parameters = {
        "limit": request.limit,
        "since_days": request.since_days,
        "workspace_id": request.workspace_id,
        "agent_id": request.agent_id,
        "embedding_model": MODEL_NAME,
        "embedding_backend": FAQ_EMBEDDING_BACKEND,
        "cluster_eps": FAQ_CLUSTER_EPS,
        "min_cluster_size": FAQ_MIN_CLUSTER_SIZE,
        "min_cluster_support": FAQ_MIN_CLUSTER_SUPPORT,
        "min_cluster_cohesion": FAQ_MIN_CLUSTER_COHESION,
        "min_generation_confidence": FAQ_MIN_GENERATION_CONFIDENCE,
        "skip_existing": FAQ_SKIP_EXISTING,
        "duplicate_threshold": FAQ_DUPLICATE_THRESHOLD,
        "llm_enabled": FAQ_LLM_ENABLED,
        "llm_model": FAQ_LLM_MODEL,
        "thinking_disabled": FAQ_LLM_THINKING_DISABLED,
        "candidate_harvest_mode": FAQ_CANDIDATE_HARVEST_MODE,
        "allow_two_example_clusters": FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS,
        "two_example_min_cohesion": FAQ_TWO_EXAMPLE_MIN_COHESION,
    }
    run_id = create_pipeline_run(parameters, workspace_id=request.workspace_id)
    try:
        conversations, ingest_metrics = fetch_conversation_records_with_metrics(
            limit=request.limit,
            since_days=request.since_days,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
        )
        candidates, stats = build_suggestion_candidates(
            conversations,
            existing_faqs_by_company=load_existing_faqs_by_company(),
            previous_candidates_by_company=load_previous_candidates_by_company(),
            run_id=run_id,
            ingest_metrics=ingest_metrics,
        )
        stats["workspace_id"] = request.workspace_id
        stats["agent_id"] = request.agent_id
        stats["since_days"] = request.since_days
        stats["embedding_model"] = current_embedding_model_label()
        stats["embedding_model_configured"] = MODEL_NAME
        for candidate in candidates:
            candidate.setdefault("candidate_metadata", {})["since_days"] = request.since_days
            candidate["candidate_metadata"]["workspace_id"] = request.workspace_id or candidate.get("workspace_id")
        suggestions = persist_pipeline_results(run_id, candidates, stats)
        log_pipeline_quality_summary(stats)
        finish_pipeline_run(run_id, "succeeded", f"Generados {len(suggestions)} candidatos.")
        summary = SuggestionSummary(
            company_count=stats["company_count"],
            cluster_count=len(suggestions),
            total_examples=stats["total_examples"],
            average_cluster_size=round(stats["total_examples"] / len(suggestions), 2) if suggestions else 0.0,
            silhouette_score=stats["silhouette_score"],
            suggestions=suggestions,
            run_id=run_id,
            **summary_metric_payload(stats),
        )
        save_json(summary.model_dump(), SUGGESTIONS_PATH)
        return summary
    except Exception as exc:
        finish_pipeline_run(run_id, "failed", str(exc))
        raise


def load_conversation_pairs() -> List[Dict[str, Any]]:
    if not CONVERSATIONS_PATH.exists():
        raise FileNotFoundError(f"Conversation file not found: {CONVERSATIONS_PATH}")
    return load_json_lines(CONVERSATIONS_PATH)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "suggestion",
        "source_schema": FAQ_SCHEMA,
        "embedding_model": MODEL_NAME,
        "embedding_backend": EMBEDDING_BACKEND_READY,
        "answer_model": FAQ_LLM_MODEL if ANSWER_GENERATOR else "historical_fallback",
        "thinking_disabled": FAQ_LLM_THINKING_DISABLED,
    }


@app.get("/workspaces", response_model=List[WorkspaceResponse])
def get_workspaces() -> List[WorkspaceResponse]:
    return list_workspaces_from_db()


@app.post("/suggest", response_model=SuggestionSummary)
def suggest(request: Optional[IngestRequest] = None) -> SuggestionSummary:
    try:
        return run_suggestion_pipeline(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo generar sugerencias: {exc}") from exc


@app.get("/suggestions", response_model=SuggestionSummary)
def get_suggestions(
    status: Optional[str] = None,
    workspace_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    include_all: bool = False,
) -> SuggestionSummary:
    if status and status not in {"pending", "approved", "rejected", "needs_review"}:
        raise HTTPException(status_code=400, detail="Invalid status filter.")
    return list_suggestions_from_db(
        status=status,
        workspace_id=workspace_id,
        agent_id=agent_id,
        include_all=include_all,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("suggestion_service:app", host="127.0.0.1", port=8003, log_level="info")
