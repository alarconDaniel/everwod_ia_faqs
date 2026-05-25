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

from app.pipeline.cleaning import (
    EMAIL_PATTERN,
    EMOJI_PATTERN,
    PHONE_PATTERN,
    is_internal_tool_trace,
    normalize_chat_text,
    word_count,
)

def is_good_faq_candidate(text: str, mode: Optional[str] = None) -> bool:
    """
    Decide si un mensaje de usuario debe entrar al clustering como posible fuente de FAQ.

    Filosofía:
    - NO usar listas de temas de negocio.
    - NO exigir prefijos de pregunta.
    - NO intentar saber si habla de envío, precios, tallas, productos, clases, comida, etc.
    - Solo eliminar ruido evidente.

    La decisión semántica real debe ocurrir después:
    clustering -> LLM -> alignment con cluster -> soporte de evidencia -> needs_review/pending.
    """
    raw_text = normalize_text(text)
    raw_folded = fold_text(raw_text).strip(" ¿?¡!.,;:-_")

    courtesy_noise_patterns = (
        r"^(hola+|buenas|buenos dias|buenos días|buenas tardes|buenas noches)(\s+.*)?$",
        r"^(hello|hi|hey|good morning|good afternoon|good evening)(\s+.*)?$",
        r"^(gracias|muchas gracias|mil gracias|thank you|thanks|thx)(\s+.*)?$",
        r"^(ok|okay|oki|dale|listo|perfecto|vale)(\s+gracias|\s+thank you|\s+thanks)?$",
    )

    if any(re.match(pattern, raw_folded, flags=re.IGNORECASE) for pattern in courtesy_noise_patterns):
        return False    
    clean = normalize_chat_text(raw_text, for_embedding=True)
    folded = fold_text(clean).strip(" ¿?¡!.,;:-_")
    words = clean.split()
    word_count_value = len(words)

    if not clean:
        return False

    # Muy corto: normalmente no aporta intención.
    if len(clean) < 6 or word_count_value < 2:
        return False

    # Muy largo: suele ser conversación completa, queja larga, respuesta pegada o ruido.
    # No lo matamos por tema, sino por forma.
    if len(clean) > 320 or word_count_value > 55:
        return False

    if is_internal_tool_trace(clean):
        return False

    # Si después de quitar emojis no queda texto, es ruido.
    if not EMOJI_PATTERN.sub("", clean).strip():
        return False

    # Rechazar mensajes cuyo único propósito sea compartir datos de contacto.
    raw_has_email = bool(EMAIL_PATTERN.search(raw_text))
    raw_has_phone = bool(PHONE_PATTERN.search(raw_text))
    clean_has_email = bool(EMAIL_PATTERN.search(clean))
    clean_has_phone = bool(PHONE_PATTERN.search(clean))

    contact_intro_patterns = (
        r"^(mi\s+)?correo\s+(es\s+)?",
        r"^(mi\s+)?email\s+(es\s+)?",
        r"^(my\s+)?email\s+(is\s+)?",
        r"^(mi\s+)?telefono\s+(es\s+)?",
        r"^(mi\s+)?tel[eé]fono\s+(es\s+)?",
        r"^(mi\s+)?celular\s+(es\s+)?",
        r"^(my\s+)?phone\s+(number\s+)?(is\s+)?",
        r"^(te\s+dejo|le\s+dejo|env[ií]o|mando|comparto)\s+",
    )

    text_without_contact = EMAIL_PATTERN.sub("", raw_folded)
    text_without_contact = PHONE_PATTERN.sub("", text_without_contact)
    text_without_contact = normalize_text(text_without_contact.strip(" -:;,.!?¡¿"))

    looks_like_contact_only = (
        (raw_has_email or raw_has_phone or clean_has_email or clean_has_phone)
        and (
            not text_without_contact
            or any(re.match(pattern, text_without_contact, flags=re.IGNORECASE) for pattern in contact_intro_patterns)
            or text_without_contact in {"mi correo es", "mi email es", "my email is", "mi telefono es", "mi celular es"}
        )
    )

    if looks_like_contact_only:
        return False

    without_email = EMAIL_PATTERN.sub("", clean)
    without_phone = PHONE_PATTERN.sub("", without_email)
    if not without_phone.strip(" -:;,.!?¡¿"):
        return False

    # Debe tener una cantidad mínima de letras reales.
    alpha_chars = re.findall(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ]", clean)
    if len(alpha_chars) < 5:
        return False

    # Rechazar respuestas triviales frecuentes.
    trivial_messages = {
        "si",
        "sí",
        "no",
        "ok",
        "okay",
        "oki",
        "dale",
        "listo",
        "bueno",
        "vale",
        "perfecto",
        "gracias",
        "muchas gracias",
        "mil gracias",
        "thank you",
        "thanks",
        "thx",
        "yes",
        "nope",
        "hello",
        "hi",
        "hey",
        "hola",
        "buenas",
        "buenos dias",
        "buenos días",
        "buenas tardes",
        "buenas noches",
    }

    if folded in trivial_messages:
        return False

    # Rechazar mensajes compuestos casi solo por saludos/cortesía.
    courtesy_only_patterns = (
        r"^(hola+|buenas|buenos dias|buenos días|buenas tardes|buenas noches)[\s,!.¿?]*$",
        r"^(hello|hi|hey|good morning|good afternoon|good evening)[\s,!.¿?]*$",
        r"^(gracias|muchas gracias|mil gracias|thank you|thanks|thx)[\s,!.¿?]*$",
    )

    if any(re.match(pattern, folded, flags=re.IGNORECASE) for pattern in courtesy_only_patterns):
        return False

    # Rechazar risas o ruido sin intención.
    if re.fullmatch(r"(j+a+|j+e+|h+a+|x+d+|lol+|lmao+)+", folded):
        return False

    # Rechazar mensajes con demasiada repetición de un mismo carácter/patrón.
    compact = re.sub(r"\s+", "", folded)
    if len(compact) >= 8:
        most_common_char_count = Counter(compact).most_common(1)[0][1]
        if most_common_char_count / len(compact) > 0.65:
            return False

    # Rechazar mensajes que parecen solo confirmación o seguimiento de caso individual.
    # Esto NO es lista de temas; es lista de formas conversacionales pobres.
    low_value_case_fragments = (
        "ya te envie",
        "ya te envié",
        "ya envie",
        "ya envié",
        "ya pague",
        "ya pagué",
        "ya hice el pago",
        "te mande",
        "te mandé",
        "te comparti",
        "te compartí",
        "adjunto comprobante",
        "envio comprobante",
        "envío comprobante",
        "mando soporte",
        "ya quedo",
        "ya quedó",
        "quedo atento",
        "quedó atento",
    )

    if any(fragment in folded for fragment in low_value_case_fragments):
        return False

    # Si llegó hasta aquí, tiene suficiente señal para entrar al clustering.
    # No necesitamos saber el tema. El pipeline posterior decide si sirve como FAQ.
    return True
