from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core.common import get_int_env, load_json_lines, normalize_question, normalize_text, save_json
from app.core.config import (
    CONVERSATIONS_PATH,
    FAQ_ALLOW_TWO_EXAMPLE_CLUSTERS,
    FAQ_CLUSTER_EPS,
    FAQ_DUPLICATE_THRESHOLD,
    FAQ_EMBEDDING_BACKEND,
    FAQ_LLM_ENABLED,
    FAQ_LLM_MODEL,
    FAQ_LLM_THINKING_DISABLED,
    FAQ_MIN_CLUSTER_COHESION,
    FAQ_MIN_CLUSTER_SIZE,
    FAQ_MIN_CLUSTER_SUPPORT,
    FAQ_MIN_QUESTION_EVIDENCE,
    FAQ_REJECT_CLUSTER_COHESION,
    FAQ_REJECT_CLUSTER_SUPPORT,
    FAQ_SKIP_EXISTING,
    FAQ_TWO_EXAMPLE_MIN_COHESION,
    MODEL_NAME,
    SUGGESTIONS_PATH,
    FAQ_MIN_GENERATION_CONFIDENCE,
    FAQ_CANDIDATE_HARVEST_MODE
)
from app.core.models import IngestRequest, SuggestionSummary
from app.pipeline.candidate_filter import is_good_faq_candidate
from app.pipeline.cleaning import (
    clean_answer_evidence,
    clean_question_evidence,
    company_key,
    is_answer_evidence_usable,
    most_common_answer,
    normalize_chat_text,
    parse_datetime,
    select_support_examples_for_candidate,
)
from app.pipeline.clustering import cluster_embeddings, compute_silhouette
from app.pipeline.embeddings import (
    _dot,
    _mean_vector,
    _normalize_vector,
    current_embedding_model_label,
    encode_texts,
)
import app.pipeline.generation as generation_module
from app.pipeline.generation import (
    build_prudent_answer,
    build_prudent_question,
    generate_faq_candidate_with_llm,
    recover_unsupported_generation_for_review,
    repair_faq_candidate_with_llm,
)
from app.pipeline.quality import (
    QUALITY_METRIC_KEYS,
    calculate_cluster_score,
    can_persist_generation_for_review,
    classify_quality_tier,
    cluster_intent_categories,
    derive_cluster_intent_statement,
    empty_quality_metrics,
    is_canonical_question_aligned_with_cluster,
    is_generation_supported_by_evidence,
    is_mixed_intent_cluster,
    is_valid_canonical_answer,
    is_valid_canonical_question,
    is_valid_cluster_intent_statement,
    is_valid_knowledge_statement,
    validate_generated_candidate,
)
from app.repository.faq_repository import (
    _candidate_to_response,
    create_pipeline_run,
    fetch_conversation_records,
    fetch_conversation_records_with_metrics,
    finish_pipeline_run,
    is_existing_faq,
    load_existing_faqs_by_company,
    load_previous_candidates_by_company,
    persist_pipeline_results,
    summary_metric_payload,
)

def _most_common_agent_id(items: Iterable[Dict[str, Any]]) -> Optional[str]:
    values = [str(item["agent_id"]) for item in items if item.get("agent_id")]
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]

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

    if len(labels) != len(embeddings):
        print(
            "Advertencia: cluster_embeddings devolvió una cantidad de labels distinta "
            f"a embeddings. labels={len(labels)}, embeddings={len(embeddings)}. "
            "Se ajustará la longitud para evitar errores."
        )

        if len(labels) > len(embeddings):
            labels = labels[: len(embeddings)]
        else:
            labels = labels + ([-1] * (len(embeddings) - len(labels)))

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

        raw_knowledge_statement = generation.get("knowledge_statement")
        knowledge_statement = (
            normalize_text(raw_knowledge_statement)
            if is_valid_knowledge_statement(raw_knowledge_statement or "")
            else None
        )

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
                    "llm_model": FAQ_LLM_MODEL if generation_module.ANSWER_GENERATOR else "unavailable",
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
