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


def parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None

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
