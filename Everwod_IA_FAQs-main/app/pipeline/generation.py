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

from app.pipeline.candidate_filter import is_good_faq_candidate
from app.pipeline.cleaning import (
    clean_answer_evidence,
    clean_canonical_answer,
    clean_canonical_question,
    clean_faq_answer,
    clean_question_evidence,
    fallback_unpublishable,
    is_answer_evidence_usable,
    support_tokens,
)
from app.pipeline.embeddings import load_embedding_model
from app.pipeline.quality import (
    is_canonical_question_aligned_with_cluster,
    clean_statement,
    derive_cluster_intent_statement,
    is_valid_cluster_intent_statement,
    is_valid_canonical_answer,
    is_valid_canonical_question,
    is_valid_knowledge_statement,
    validate_generated_candidate,
)

ANSWER_GENERATOR: Optional[Any] = None
ANSWER_GENERATOR_READY = False
MODELS_READY = False

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

def strip_thinking_text(raw_text: str) -> str:
    text = str(raw_text or "")
    text = re.sub(r"(?is)<think>.*?</think>", "", text)
    text = re.sub(r"(?is)^.*?</think>", "", text)
    return normalize_text(text)

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
