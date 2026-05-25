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
    PLACEHOLDER_PATTERN,
    RAW_URL_PATTERN,
    SUPPORT_STOPWORDS,
    clean_answer_evidence,
    clean_faq_answer,
    clean_question_evidence,
    deduplicate_support_example_texts,
    has_case_specific_time_reference,
    has_emoji,
    is_answer_evidence_usable,
    is_internal_tool_trace,
    is_low_quality_chat_style,
    select_support_examples_for_candidate,
    support_tokens,
    support_tokens_multilingual,
    word_count,
)
from app.pipeline.embeddings import _dot, _mean_vector, _normalize_vector, encode_texts

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

def validate_generated_candidate(candidate: Dict[str, Any]) -> Tuple[bool, str]:
    if not candidate.get("publish"):
        return False, "llm_publish_false"

    if float(candidate.get("confidence") or 0) < FAQ_MIN_GENERATION_CONFIDENCE:
        return False, "rejected_generation_confidence"

    if not is_valid_knowledge_statement(candidate.get("knowledge_statement", "")):
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

def can_persist_generation_for_review(
    generation: Dict[str, Any],
    rejection_reason: str,
    intent_categories: set[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Decide si una salida imperfecta se puede guardar para revisión humana.

    No depende exclusivamente de categorías conocidas, porque el sistema debe
    funcionar con negocios y temas no anticipados.
    """
    if not generation.get("publish"):
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

    # Caso principal: pregunta buena, pero respuesta/statement/evidencia imperfecta.
    if question_ok and rejection_reason in recoverable_reasons:
        if not answer_ok:
            from app.pipeline.generation import build_prudent_answer

            fallback_answer = build_prudent_answer([question], intent_categories)
            generation["canonical_answer"] = fallback_answer or "La respuesta requiere revisión humana antes de publicarse."

        if not is_valid_knowledge_statement(generation.get("knowledge_statement", "")):
            generation["knowledge_statement"] = None

        candidate_kind = (
            "question_with_answer_review"
            if rejection_reason in {
                "rejected_invalid_canonical_answer",
                "rejected_answer_not_supported_by_evidence",
                "rejected_question_answer_misaligned",
            }
            else "full_faq"
        )

        return True, "pregunta detectada con respuesta a revisar", candidate_kind

    # Caso secundario: respuesta buena, pregunta mala. Esto debe pasar por repair,
    # no persistirse directo.
    if answer_ok and rejection_reason == "rejected_invalid_canonical_question":
        return False, None, None

    return False, None, None

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
