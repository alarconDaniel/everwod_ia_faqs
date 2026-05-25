import pytest

import app.pipeline.candidate_filter as candidate_filter
import app.pipeline.cleaning as cleaning
import app.pipeline.embeddings as embeddings
import app.pipeline.generation as generation
import app.pipeline.pipeline as pipeline
import app.pipeline.quality as quality
import app.repository.faq_repository as faq_repository
from app.core import common, config


class FakeTokenizer:
    eos_token_id = 0

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        return "prompt"


class FakeGenerator:
    tokenizer = FakeTokenizer()

    def __init__(self, text):
        self.text = text

    def __call__(self, *args, **kwargs):
        return [{"generated_text": self.text}]


class FakeSequenceGenerator:
    tokenizer = FakeTokenizer()

    def __init__(self, texts):
        self.texts = list(texts)
        self.index = 0

    def __call__(self, *args, **kwargs):
        text = self.texts[min(self.index, len(self.texts) - 1)]
        self.index += 1
        return [{"generated_text": text}]


@pytest.fixture(autouse=True)
def lightweight_models(monkeypatch):
    generation.MODELS_READY = True
    embeddings.EMBEDDING_MODEL_READY = True
    generation.ANSWER_GENERATOR_READY = True
    embeddings.EMBEDDING_MODEL = None
    generation.ANSWER_GENERATOR = None
    embeddings.EMBEDDING_BACKEND_READY = "hash"
    monkeypatch.setattr(generation, "load_models", lambda: None)


def patch_pipeline_encode_texts(monkeypatch, encoder):
    monkeypatch.setattr(pipeline, "encode_texts", encoder)
    monkeypatch.setattr(quality, "encode_texts", encoder)


def test_candidate_filter_rejects_noise_and_accepts_business_question():
    assert not candidate_filter.is_good_faq_candidate("hola")
    assert not candidate_filter.is_good_faq_candidate("Gracias por la informacion")
    assert not candidate_filter.is_good_faq_candidate("mi correo es cliente@example.com")
    assert candidate_filter.is_good_faq_candidate("Quiero reservar una clase de cortesia")


def test_raw_question_can_be_cleaned_to_canonical_question():
    question = cleaning.clean_canonical_question("Quisiera agendar una clase de prueba mañana 6.30 am")

    assert "mañana" not in question.lower()
    assert "6.30" not in question
    assert question == "¿Cómo puedo agendar una clase de prueba?"
    assert quality.is_valid_canonical_question(question)


def test_half_payment_raw_question_can_be_canonicalized():
    question = cleaning.clean_canonical_question("Puedo pagar con la mitad")

    assert question == "¿Es posible reservar pagando un anticipo?"
    assert quality.is_valid_canonical_question(question)


def test_existing_faq_deduplicates_exact_normalized_question():
    assert faq_repository.is_existing_faq(
        "¿Cómo puedo reservar una clase?",
        ["Como puedo reservar una clase"],
    )


def test_redacts_pii_from_examples():
    text = "Hola Juan Perez, escribe a cliente@example.com o al 300 123 4567"
    redacted = cleaning.redact_personal_data(text)

    assert "Juan Perez" not in redacted
    assert "cliente@example.com" not in redacted
    assert "300 123 4567" not in redacted
    assert "[correo]" in redacted
    assert "[telefono]" in redacted


@pytest.mark.parametrize(
    "answer",
    [
        "Tu tóxica de confianza 😏; pagas directo en www.severasmedias.com",
        "¡Hola! Mi nombre es Luciana 😊 Estoy aquí para ayudarte con tus desayunos.",
        "¿Para qué ciudad lo necesitas?",
        "Envíame el soporte para dejarte confirmado el jueves a las 7:00 am.",
    ],
)
def test_conversational_answers_are_rejected(answer):
    assert not quality.is_valid_canonical_answer(answer)


def test_placeholders_are_rejected():
    assert not quality.is_valid_canonical_answer("La reserva se confirma llamando a [telefono].")
    assert not quality.is_valid_canonical_answer("La respuesta se envia al [correo] registrado.")


def test_tool_traces_are_rejected():
    trace = "Reasoning function_call getSchedules."

    assert cleaning.is_internal_tool_trace(trace)
    assert not quality.is_valid_canonical_answer(trace)


def test_clean_faq_answer_removes_personal_greeting_and_name():
    answer = "Hola Juan, para tu día de cortesía te ayudamos a reservar por WhatsApp"

    cleaned = cleaning.clean_faq_answer(answer)

    assert "Hola" not in cleaned
    assert "Juan" not in cleaned
    assert "te ayudamos" not in cleaned.lower()
    assert cleaned.startswith("Para el día de cortesía")


def test_parse_valid_llm_json():
    raw = '{"publish": true, "knowledge_statement": "Los pagos por Nequi pueden realizarse cuando el negocio tenga este metodo habilitado.", "canonical_question": "¿Cómo puedo pagar por Nequi?", "canonical_answer": "Los pagos por Nequi pueden realizarse cuando el negocio tenga este metodo habilitado.", "confidence": 0.82, "reason": "Evidencia consistente."}'

    parsed = generation.parse_llm_faq_candidate(raw)

    assert parsed is not None
    assert parsed["publish"] is True
    assert parsed["canonical_question"] == "¿Cómo puedo pagar por Nequi?"
    assert parsed["knowledge_statement"].startswith("Los pagos por Nequi")
    assert parsed["confidence"] == 0.82


def test_parse_llm_json_removes_qwen3_thinking_tags():
    raw = '<think>razonamiento interno</think>{"publish": true, "knowledge_statement": "El domicilio es gratis desde $130.000.", "canonical_question": "¿Desde qué valor el domicilio es gratis?", "canonical_answer": "El domicilio es gratis desde $130.000.", "confidence": 0.82, "reason": "Evidencia consistente."}'

    parsed = generation.parse_llm_faq_candidate(raw)

    assert parsed is not None
    assert "<think>" not in parsed["canonical_answer"].lower()
    assert parsed["canonical_question"] == "¿Desde qué valor el domicilio es gratis?"


def test_parse_repairs_zero_confidence_when_reason_is_clear():
    raw = '{"publish": true, "knowledge_statement": "No hay punto fisico; el negocio opera online.", "canonical_question": "¿Tienen punto físico?", "canonical_answer": "No hay punto fisico; el negocio opera online.", "confidence": 0.0, "reason": "La intención es clara y la evidencia es directa."}'

    parsed = generation.parse_llm_faq_candidate(raw)

    assert parsed is not None
    assert parsed["confidence"] == 0.45
    assert parsed["confidence_was_repaired"] is True


def test_default_llm_model_is_qwen3():
    assert config.FAQ_LLM_MODEL == "Qwen/Qwen3-1.7B"


def test_quality_summary_logs_llm_model_and_thinking_state(capsys):
    pipeline.log_pipeline_quality_summary({"workspace_id": 126, "since_days": 365})

    output = capsys.readouterr().out
    assert "LLM model active: Qwen/Qwen3-1.7B" in output
    assert "Thinking disabled: True" in output


def test_free_delivery_question_must_align_with_answer():
    aligned, reason = quality.is_question_answer_aligned(
        "¿Qué vale el domicilio?",
        "El domicilio es gratis en compras superiores a $130.000 dentro de la zona de cobertura.",
        "El domicilio es gratis en compras superiores a $130.000 dentro de la zona de cobertura.",
    )

    assert not aligned
    assert reason == "rejected_question_answer_misaligned"

    aligned, _reason = quality.is_question_answer_aligned(
        "¿Desde qué valor el domicilio es gratis?",
        "El domicilio es gratis en compras superiores a $130.000 dentro de la zona de cobertura.",
        "El domicilio es gratis en compras superiores a $130.000 dentro de la zona de cobertura.",
    )

    assert aligned


def test_generation_with_unsupported_delivery_fact_is_rejected():
    generation = {
        "publish": True,
        "knowledge_statement": "El domicilio es gratis en compras superiores a $130.000.",
        "canonical_question": "¿Desde qué valor el domicilio es gratis?",
        "canonical_answer": "El domicilio es gratis en compras superiores a $130.000.",
        "confidence": 0.86,
    }

    support = quality.is_generation_supported_by_evidence(
        generation,
        questions=["¿Puedo ver el diseño antes de imprimir?"],
        answers=["Se puede revisar el diseño antes de imprimirlo."],
    )

    assert support["should_hard_reject"]
    assert support["reason"] == "rejected_answer_not_supported_by_evidence"


def test_positive_generation_contradicted_by_negative_evidence_is_rejected():
    generation = {
        "publish": True,
        "knowledge_statement": "Se puede personalizar ropa interior de mujer.",
        "canonical_question": "¿Se puede personalizar ropa interior de mujer?",
        "canonical_answer": "Sí, se puede personalizar ropa interior de mujer.",
        "confidence": 0.85,
    }

    support = quality.is_generation_supported_by_evidence(
        generation,
        questions=["¿Se puede personalizar ropa interior de mujer?"],
        answers=["Por ahora no personalizamos ropa interior de mujer, pero sí tenemos boxers personalizados."],
    )

    assert support["should_hard_reject"]
    assert support["reason"] == "rejected_answer_not_supported_by_evidence"


def test_valid_canonical_question_accepts_business_intent_without_narrow_prefix():
    assert quality.is_valid_canonical_question("\u00bfOfreces disenos de medias para pareja?")
    assert quality.is_valid_canonical_question("\u00bfHacen envios a Bucaramanga?")
    assert quality.is_valid_canonical_question("\u00bfTienen medias de Superman?")
    assert quality.is_valid_canonical_question("\u00bfRealizan personalizacion con logo?")
    assert quality.is_valid_canonical_question("\u00bfManejan medias en talla XL?")
    assert quality.is_valid_canonical_question("\u00bfExiste opcion de recogida fisica?")


def test_partial_evidence_becomes_review_instead_of_hard_reject():
    generation = {
        "publish": True,
        "knowledge_statement": "La cobertura de envios puede variar segun la ciudad y debe confirmarse para cada pedido.",
        "canonical_question": "\u00bfHacen envios a Bucaramanga?",
        "canonical_answer": "La cobertura de envios puede variar segun la ciudad y debe confirmarse para cada pedido.",
        "confidence": 0.85,
    }

    support = quality.is_generation_supported_by_evidence(
        generation,
        questions=["Hacen envios a Bucaramanga?", "Envian a Bucaramanga?"],
        answers=["Podemos revisar cobertura segun ciudad."],
    )

    assert not support["should_hard_reject"]
    assert support["support_level"] in {"partial", "weak"}
    assert support["requires_answer_review"] is True


def test_parse_llm_json_surrounded_by_text():
    raw = 'Claro. {"publish": false, "canonical_question": "", "canonical_answer": "", "confidence": 0.21, "reason": "Cluster mezclado."} listo'

    parsed = generation.parse_llm_faq_candidate(raw)

    assert parsed is not None
    assert parsed["publish"] is False
    assert parsed["reason"] == "Cluster mezclado."


def test_invalid_llm_json_returns_none():
    assert generation.parse_llm_faq_candidate("{publish: true") is None


def test_publish_false_is_not_valid_generation():
    parsed = generation.parse_llm_faq_candidate(
        '{"publish": false, "canonical_question": "", "canonical_answer": "", "confidence": 0.25, "reason": "No hay evidencia."}'
    )

    is_valid, reason = quality.validate_generated_candidate(parsed)

    assert not is_valid
    assert reason == "llm_publish_false"


def test_incoherent_cluster_is_rejected():
    mixed, reason = quality.is_mixed_intent_cluster(
        [
            "Hola tienen los desayunos sorpresa?",
            "Los precios ya incluyen el domicilio?",
            "Que precio tiene el envio a Chia?",
        ]
    )

    assert mixed
    assert "mezcla" in reason.lower()


def test_fallback_without_qwen_does_not_publish_raw_dump_answer():
    result = generation.generate_faq_candidate_with_llm(
        company_name="Empire Box",
        questions=["Cómo hago para pagar por Nequi?"],
        historical_answers=["Tu tóxica de confianza 😏; pagas directo y te paso el link."],
        cluster_metrics={"cluster_cohesion": 0.9},
        recurrence=3,
    )

    assert result["publish"] is False
    assert result["canonical_answer"] == ""


def test_llm_generates_complete_publishable_candidate(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "canonical_question": "¿Cómo puedo agendar una clase de prueba?", "canonical_answer": "Las clases de prueba pueden agendarse segun la disponibilidad y los horarios definidos por el negocio.", "confidence": 0.87, "reason": "El cluster trata sobre agendamiento de clases de prueba."}'
    )

    result = generation.generate_faq_candidate_with_llm(
        company_name="Empire Box",
        questions=[
            "Quisiera agendar una clase de prueba mañana 6.30 am",
            "Quiero reservar una clase de prueba",
            "Como puedo agendar clase de cortesia?",
        ],
        historical_answers=[
            "La clase de prueba se agenda segun disponibilidad del negocio.",
            "Las reservas de clase de prueba dependen de los horarios disponibles.",
        ],
        cluster_metrics={"cluster_cohesion": 0.9},
        recurrence=3,
    )

    is_valid, reason = quality.validate_generated_candidate(result)

    assert result["publish"] is True
    assert result["canonical_question"] == "¿Cómo puedo agendar una clase de prueba?"
    assert is_valid, reason


def test_build_company_candidates_uses_canonical_question_and_answer(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "canonical_question": "¿Cómo puedo agendar una clase de prueba?", "canonical_answer": "Las clases de prueba pueden agendarse segun la disponibilidad y los horarios definidos por el negocio.", "confidence": 0.9, "reason": "Evidencia consistente."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 74,
            "company_id": "74",
            "company_name": "Empire Box",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "La clase de prueba se agenda segun disponibilidad del negocio.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            [
                "Quisiera agendar una clase de prueba mañana 6.30 am",
                "Quiero reservar una clase de prueba",
                "Como puedo agendar clase de cortesia?",
            ],
            start=1,
        )
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert candidates[0]["normalized_question"] == "¿Cómo puedo agendar una clase de prueba?"
    assert candidates[0]["suggested_answer"].startswith("Las clases de prueba")
    assert candidates[0]["candidate_metadata"]["generation_confidence"] == 0.9
    assert stats["clusters_valid"] == 1


def test_half_payment_cluster_becomes_candidate(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "canonical_question": "¿Puedo pagar con anticipo?", "canonical_answer": "Sí. La reserva puede confirmarse con un anticipo cuando esta modalidad esté habilitada.", "confidence": 0.74, "reason": "El cluster repite dudas sobre anticipo."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 74,
            "company_id": "74",
            "company_name": "Tienda",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "La reserva puede confirmarse con un anticipo.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            ["Puedo pagar con la mitad", "¿Puedo separar con anticipo?", "¿Se reserva abonando?"],
            start=1,
        )
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert candidates[0]["candidate_metadata"]["quality_tier"] == "high_confidence"
    assert stats["accepted_candidates"] == 1


def test_scheduling_cluster_removes_case_specific_dates(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "canonical_question": "¿Cómo agendo una clase de prueba?", "canonical_answer": "Las clases de prueba pueden agendarse según la disponibilidad del negocio.", "confidence": 0.73, "reason": "Preguntas recurrentes sobre agendamiento."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 74,
            "company_id": "74",
            "company_name": "Empire Box",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "Las clases de prueba se agendan según disponibilidad.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            [
                "Quisiera agendar una clase de prueba mañana 6.30 am",
                "Puedo agendar una clase de prueba para el lunes",
                "Deseo reservar una clase de cortesía",
            ],
            start=1,
        )
    ]

    candidates, _stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert candidates[0]["normalized_question"] == "¿Cómo agendo una clase de prueba?"
    assert "mañana" not in candidates[0]["normalized_question"].lower()


def test_formal_nequi_faq_passes_validation():
    assert quality.is_valid_canonical_question("¿Cómo puedo pagar por Nequi?")
    assert quality.is_valid_canonical_answer(
        "Los pagos por Nequi pueden realizarse cuando el negocio tenga este método habilitado."
    )


def test_meta_historical_answer_is_rejected():
    assert not quality.is_valid_canonical_answer(
        "No hay respuestas históricamente confirmadas sobre cómo personalizar ropa interior."
    )


def test_followup_or_contact_style_answers_are_rejected():
    assert not quality.is_valid_canonical_answer(
        "El costo depende del pedido. Por favor, indique los detalles para un calculo exacto."
    )
    assert not quality.is_valid_canonical_answer(
        "Para informacion mas detallada, contacte al servicio de atencion al cliente."
    )


def test_medium_confidence_is_persistable_as_needs_review(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "canonical_question": "¿Qué medios de pago aceptan?", "canonical_answer": "Los medios de pago disponibles dependen de la configuración comercial del negocio.", "confidence": 0.55, "reason": "Intención clara con confianza media."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 74,
            "company_id": "74",
            "company_name": "Tienda",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "Los medios de pago disponibles dependen del negocio.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            ["¿Qué medios de pago aceptan?", "¿Puedo pagar por Nequi?", "¿Reciben Daviplata?"],
            start=1,
        )
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert candidates[0]["candidate_metadata"]["quality_tier"] == "needs_review"
    assert stats["accepted_needs_review_candidates"] == 1


def test_prudent_whatsapp_personalization_answer_persists_as_needs_review(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "knowledge_statement": "Las opciones de personalizacion por WhatsApp pueden variar y deben confirmarse con el negocio.", "canonical_question": "\u00bfComo se personaliza las medias por WhatsApp?", "canonical_answer": "Las opciones de personalizacion por WhatsApp pueden variar y deben confirmarse con el negocio.", "confidence": 0.85, "reason": "Intencion recurrente con respuesta prudente."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 126,
            "company_id": "126",
            "company_name": "Severas Medias",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "Escribe por WhatsApp para revisar la personalizacion.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            ["Como personalizo medias por WhatsApp?", "Personalizar medias por WhatsApp"],
            start=1,
        )
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert candidates[0]["status"] == "needs_review"
    assert candidates[0]["candidate_metadata"]["candidate_kind"] == "question_with_answer_review"
    assert candidates[0]["candidate_metadata"]["requires_answer_review"] is True
    assert stats["persisted_question_with_answer_review"] == 1


def test_invalid_knowledge_statement_degrades_to_review(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "knowledge_statement": "\u00bfHacen envios a Bucaramanga?", "canonical_question": "\u00bfHacen envios a Bucaramanga?", "canonical_answer": "La cobertura de envios puede variar segun la ciudad y debe confirmarse para cada pedido.", "confidence": 0.85, "reason": "Intencion clara."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 126,
            "company_id": "126",
            "company_name": "Severas Medias",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "Revisamos cobertura segun ciudad.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(["Hacen envios a Bucaramanga?", "Envian a Bucaramanga?"], start=1)
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert candidates[0]["status"] == "needs_review"
    assert candidates[0]["candidate_metadata"]["knowledge_statement"] is None
    assert stats["repair_failed_but_persisted_for_review"] == 1


def test_question_answer_misalignment_runs_repair_pass(monkeypatch):
    generation.ANSWER_GENERATOR = FakeSequenceGenerator(
        [
            '{"publish": true, "knowledge_statement": "El domicilio es gratis en compras superiores a $130.000.", "canonical_question": "\u00bfQue vale el domicilio?", "canonical_answer": "El domicilio es gratis en compras superiores a $130.000.", "confidence": 0.88, "reason": "Evidencia consistente."}',
            '{"publish": true, "knowledge_statement": "El domicilio es gratis en compras superiores a $130.000.", "canonical_question": "\u00bfDesde que valor el domicilio es gratis?", "canonical_answer": "El domicilio es gratis en compras superiores a $130.000.", "confidence": 0.82, "reason": "Pregunta reparada.", "repaired": true}',
        ]
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0, 0])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 74,
            "company_id": "74",
            "company_name": "Tienda",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "El domicilio es gratis en compras superiores a $130.000.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            ["Desde cuanto el domicilio es gratis?", "El domicilio gratis desde que valor?"],
            start=1,
        )
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert candidates[0]["candidate_metadata"]["repaired"] is True
    assert stats["repair_attempts"] == 1
    assert stats["repair_successes"] == 1


def test_three_reasonable_synthetic_clusters_do_not_return_zero(monkeypatch):
    generation.ANSWER_GENERATOR = FakeSequenceGenerator(
        [
            '{"publish": true, "canonical_question": "¿Qué medios de pago aceptan?", "canonical_answer": "Los medios de pago disponibles dependen de la configuración comercial del negocio.", "confidence": 0.76, "reason": "Pagos recurrentes."}',
            '{"publish": true, "canonical_question": "¿El domicilio tiene costo adicional?", "canonical_answer": "El costo del domicilio puede variar según la ubicación y las condiciones del pedido.", "confidence": 0.74, "reason": "Domicilios recurrentes."}',
            '{"publish": true, "canonical_question": "¿Qué productos están disponibles?", "canonical_answer": "La disponibilidad de productos puede variar según el inventario definido por el negocio.", "confidence": 0.73, "reason": "Disponibilidad recurrente."}',
        ]
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts[:3]] + [[0.0, 1.0] for _ in texts[3:6]] + [[0.7, 0.7] for _ in texts[6:]])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0, 0, 0, 1, 1, 1, 2, 2, 2])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    texts = [
        "¿Qué medios de pago aceptan?",
        "¿Puedo pagar por Nequi?",
        "¿Reciben Daviplata?",
        "¿El domicilio tiene costo adicional?",
        "¿Cuánto cuesta el envío?",
        "¿La entrega tiene valor?",
        "¿Qué productos están disponibles?",
        "¿Tienen desayunos sorpresa?",
        "¿Hay productos para personalizar?",
    ]
    conversations = [
        {
            "workspace_id": 74,
            "company_id": "74",
            "company_name": "Tienda",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "La información depende de las condiciones definidas por el negocio.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(texts, start=1)
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 3
    assert stats["accepted_candidates"] == 3


def test_embedding_model_metadata_is_multilingual(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "canonical_question": "¿Qué medios de pago aceptan?", "canonical_answer": "Los medios de pago disponibles dependen de la configuración comercial del negocio.", "confidence": 0.76, "reason": "Pagos recurrentes."}'
    )
    embeddings.EMBEDDING_BACKEND_READY = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 74,
            "company_id": "74",
            "company_name": "Tienda",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "Los medios de pago disponibles dependen del negocio.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            ["¿Qué medios de pago aceptan?", "¿Puedo pagar por Nequi?", "¿Reciben Daviplata?"],
            start=1,
        )
    ]

    candidates, _stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert config.MODEL_NAME == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert candidates[0]["candidate_metadata"]["embedding_model"] == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert candidates[0]["normalized_question"] == "¿Qué medios de pago aceptan?"
    assert candidates[0]["suggested_answer"] == "Los medios de pago disponibles dependen de la configuración comercial del negocio."


def test_concision_limits_for_canonical_faq():
    question = "¿Cuál es el procedimiento exacto que debe seguir un cliente interesado en agendar una clase de prueba en determinada fecha?"
    answer = (
        "Las clases de prueba pueden agendarse según la disponibilidad del negocio. "
        "La confirmación del cupo depende de la agenda, del horario, de la sede, de la capacidad operativa "
        "y de otras condiciones internas que deben ser revisadas por el equipo responsable antes de confirmar. "
        "También puede requerir validaciones adicionales antes de entregar una respuesta definitiva al cliente."
    )

    assert not quality.is_valid_canonical_question(question)
    assert not quality.is_valid_canonical_answer(answer)


def test_two_example_cluster_can_be_needs_review(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "knowledge_statement": "El domicilio es gratis en compras superiores a $130.000.", "canonical_question": "¿Desde qué valor el domicilio es gratis?", "canonical_answer": "El domicilio es gratis en compras superiores a $130.000.", "confidence": 0.88, "reason": "Evidencia consistente."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0, 0])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)

    conversations = [
        {
            "workspace_id": 74,
            "company_id": "74",
            "company_name": "Tienda",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "El domicilio es gratis en compras superiores a $130.000.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            ["¿El domicilio puede ser gratis?", "¿Desde cuanto el domicilio es gratis?"],
            start=1,
        )
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert candidates[0]["candidate_metadata"]["quality_tier"] == "needs_review"
    assert stats["accepted_needs_review_candidates"] == 1


def test_duplicate_against_existing_faq_records_metrics(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "knowledge_statement": "Los pagos por Nequi estan disponibles cuando el negocio los habilita.", "canonical_question": "¿Cómo puedo pagar por Nequi?", "canonical_answer": "Los pagos por Nequi estan disponibles cuando el negocio los habilita.", "confidence": 0.82, "reason": "Evidencia consistente."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0, 0, 0])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: bool(existing))

    conversations = [
        {
            "workspace_id": 74,
            "company_id": "74",
            "company_name": "Tienda",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "Se puede pagar por Nequi cuando esta habilitado.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            ["¿Puedo pagar por Nequi?", "¿Reciben Nequi?", "¿Cómo pago por Nequi?"],
            start=1,
        )
    ]

    candidates, stats = pipeline.build_company_candidates(
        conversations,
        existing_questions=["¿Cómo puedo pagar por Nequi?"],
        run_id="run-1",
    )

    assert candidates == []
    assert stats["duplicates_against_existing_faqs"] == 1
    assert stats["candidates_skipped_existing"] == 1


def test_box_mujer_cluster_cannot_generate_fusagasuga_delivery(monkeypatch):
    generation.ANSWER_GENERATOR = FakeSequenceGenerator(
        [
            '{"publish": true, "cluster_intent_statement": "Los clientes preguntan por el box mujer.", "knowledge_statement": "La entrega es en la zona urbana de Fusagasuga.", "canonical_question": "La entrega es en la zona urbana de Fusagasuga?", "canonical_answer": "Si, la entrega es en la zona urbana de Fusagasuga.", "confidence": 0.85, "reason": "Dato tomado de una respuesta historica."}',
            '{"publish": true, "cluster_intent_statement": "Los clientes preguntan por el box mujer.", "knowledge_statement": "El box mujer puede estar disponible segun el catalogo del negocio.", "canonical_question": "Tienen box mujer disponible?", "canonical_answer": "El box mujer puede estar disponible segun el catalogo del negocio.", "confidence": 0.78, "reason": "Pregunta reparada hacia el cluster.", "repaired": true}',
        ]
    )

    def fake_encode(texts):
        vectors = []
        for text in texts:
            folded = common.fold_text(text)
            vectors.append([0.0, 1.0] if "entrega" in folded or "fusagasuga" in folded else [1.0, 0.0])
        return vectors

    patch_pipeline_encode_texts(monkeypatch, fake_encode)
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 153,
            "company_id": "153",
            "company_name": "Pizca De Amor",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": answer,
            "created_at": "2026-05-14T10:00:00",
        }
        for index, (text, answer) in enumerate(
            [
                ("Quiero el box mujer", "Excelente eleccion. Podemos continuar con los detalles del envio."),
                ("Me interesa el box mujer", "El box mujer puede revisarse en el catalogo."),
                ("Tienen box mujer", "\u00bfLa entrega es en la zona urbana de Fusagasuga?"),
            ],
            start=1,
        )
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert "Fusagasuga" not in candidates[0]["normalized_question"]
    assert "box mujer" in common.fold_text(candidates[0]["normalized_question"])
    assert candidates[0]["candidate_metadata"]["repaired"] is True
    assert stats["cluster_question_alignment_failed"] == 1
    assert stats["repair_alignment_successes"] == 1


def test_support_examples_are_deduplicated_when_question_repeats(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "cluster_intent_statement": "Los clientes preguntan por el box mujer.", "knowledge_statement": "El box mujer puede estar disponible segun el catalogo del negocio.", "canonical_question": "Tienen box mujer disponible?", "canonical_answer": "El box mujer puede estar disponible segun el catalogo del negocio.", "confidence": 0.78, "reason": "Intencion recurrente."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 153,
            "company_id": "153",
            "company_name": "Pizca De Amor",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": "Quiero el box mujer",
            "assistant_text": "El box mujer puede revisarse en el catalogo.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index in range(1, 4)
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert candidates[0]["support_examples"] == ["Quiero el box mujer"]
    assert stats["support_examples_deduplicated"] == 2
    assert candidates[0]["status"] == "needs_review"


def test_canonical_question_far_from_cluster_is_misaligned(monkeypatch):
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[0.0, 1.0] for _ in texts])

    alignment = quality.is_canonical_question_aligned_with_cluster(
        "\u00bfLa entrega es en la zona urbana de Fusagasuga?",
        ["Quiero el box mujer", "Me interesa el box mujer", "Tienen box mujer"],
        question_embeddings=[[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
        cluster_center=[1.0, 0.0],
    )

    assert alignment["status"] == "misaligned"


def test_answer_only_free_delivery_is_not_generated_from_product_cluster(monkeypatch):
    generation.ANSWER_GENERATOR = FakeSequenceGenerator(
        [
            '{"publish": true, "cluster_intent_statement": "Los clientes preguntan por el box mujer.", "knowledge_statement": "El domicilio es gratis desde $130.000.", "canonical_question": "Desde que valor el domicilio es gratis?", "canonical_answer": "El domicilio es gratis desde $130.000.", "confidence": 0.86, "reason": "Dato visto en respuestas."}',
            '{"publish": true, "cluster_intent_statement": "Los clientes preguntan por el box mujer.", "knowledge_statement": "El domicilio es gratis desde $130.000.", "canonical_question": "Desde que valor el domicilio es gratis?", "canonical_answer": "El domicilio es gratis desde $130.000.", "confidence": 0.86, "reason": "Sin reparacion util.", "repaired": true}',
        ]
    )

    def fake_encode(texts):
        vectors = []
        for text in texts:
            folded = common.fold_text(text)
            vectors.append([0.0, 1.0] if "domicilio" in folded else [1.0, 0.0])
        return vectors

    patch_pipeline_encode_texts(monkeypatch, fake_encode)
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 153,
            "company_id": "153",
            "company_name": "Pizca De Amor",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "El domicilio es gratis desde $130.000.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(["Quiero el box mujer", "Me interesa el box mujer", "Tienen box mujer"], start=1)
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert candidates == []
    assert stats["candidates_rejected_due_to_cluster_misalignment"] == 1


def test_free_delivery_cluster_can_persist_when_user_questions_match(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "cluster_intent_statement": "Los clientes preguntan por el costo o gratuidad del domicilio.", "knowledge_statement": "El domicilio es gratis desde $130.000.", "canonical_question": "Desde que valor el domicilio es gratis?", "canonical_answer": "El domicilio es gratis desde $130.000.", "confidence": 0.86, "reason": "Preguntas recurrentes sobre domicilio."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 153,
            "company_id": "153",
            "company_name": "Pizca De Amor",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "El domicilio es gratis desde $130.000.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            ["Desde que valor el domicilio es gratis?", "El domicilio gratis desde que valor?", "Los precios incluyen domicilio?"],
            start=1,
        )
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert "domicilio" in common.fold_text(candidates[0]["normalized_question"])
    assert stats["cluster_question_alignment_strong"] == 1


def test_aligned_question_with_unsupported_answer_becomes_needs_review(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "cluster_intent_statement": "Los clientes preguntan por camisetas estampadas.", "knowledge_statement": "Se ofrecen camisetas estampadas con envio gratis desde $130.000.", "canonical_question": "Ofrecen camisetas estampadas?", "canonical_answer": "Si, se ofrecen camisetas estampadas con envio gratis desde $130.000.", "confidence": 0.85, "reason": "Intencion clara."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 126,
            "company_id": "126",
            "company_name": "Severas Medias",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "Podemos revisar disponibilidad segun inventario.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(
            ["Ofrecen camisetas estampadas?", "Tienen camisetas estampadas?", "Hay camisetas estampadas?"],
            start=1,
        )
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert candidates[0]["status"] == "needs_review"
    assert candidates[0]["candidate_metadata"]["review_reason_code"] in {
        "answer_partial_support",
        "answer_unsupported_recovered_for_review",
        "question_answer_misaligned_recovered_for_review",
    }
    assert "inventario" in common.fold_text(candidates[0]["suggested_answer"])
    assert stats["accepted_needs_review_candidates"] == 1


def test_recovered_partial_alignment_candidate_stays_editable_for_review(monkeypatch):
    generation.ANSWER_GENERATOR = FakeGenerator(
        '{"publish": true, "cluster_intent_statement": "Los clientes preguntan por medios de pago.", "knowledge_statement": "Los medios de pago dependen de la configuracion comercial del negocio.", "canonical_question": "Que medios de pago aceptan?", "canonical_answer": "Los medios de pago dependen de la configuracion comercial del negocio.", "confidence": 0.55, "reason": "Confianza media."}'
    )
    patch_pipeline_encode_texts(monkeypatch, lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline, "cluster_embeddings", lambda embeddings: [0 for _ in embeddings])
    monkeypatch.setattr(pipeline, "compute_silhouette", lambda embeddings, labels: None)
    monkeypatch.setattr(pipeline, "is_existing_faq", lambda question, existing: False)

    conversations = [
        {
            "workspace_id": 126,
            "company_id": "126",
            "company_name": "Severas Medias",
            "agent_id": "agent-1",
            "conversation_pk": index,
            "user_message_pk": index,
            "user_text": text,
            "assistant_text": "Los medios de pago disponibles dependen del negocio.",
            "created_at": "2026-05-14T10:00:00",
        }
        for index, text in enumerate(["Puedo pagar por Nequi?", "Reciben Daviplata?", "Aceptan PSE?"], start=1)
    ]

    candidates, stats = pipeline.build_company_candidates(conversations, existing_questions=[], run_id="run-1")

    assert len(candidates) == 1
    assert candidates[0]["status"] == "needs_review"
    assert candidates[0]["candidate_metadata"]["review_reason"]
    assert stats["accepted_needs_review_candidates"] == 1
