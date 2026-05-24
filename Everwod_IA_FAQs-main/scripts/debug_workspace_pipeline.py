import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import suggestion_service as svc
from ingest_service import fetch_conversation_records_with_metrics


def cluster_debug(records: List[Dict[str, Any]], run_llm: bool = True) -> Dict[str, Any]:
    valid_items = []
    rejected_initial = 0
    for item in records:
        user_text = svc.normalize_text(item.get("user_text"))
        if user_text and svc.is_good_faq_candidate(user_text):
            valid_items.append({**item, "user_text": user_text})
        elif user_text:
            rejected_initial += 1

    result: Dict[str, Any] = {
        "pairs_processed": len(records),
        "candidate_questions_detected": len([item for item in records if svc.normalize_text(item.get("user_text"))]),
        "candidate_questions_rejected_initial_filter": rejected_initial,
        "candidate_questions_kept_for_embedding": len(valid_items),
        "embedding_model": svc.current_embedding_model_label(),
        "clusters": [],
    }
    if not valid_items:
        return result

    user_texts = [item["user_text"] for item in valid_items]
    embeddings = svc.encode_texts(user_texts)
    labels = svc.cluster_embeddings(embeddings)
    cluster_groups: Dict[int, List[int]] = {}
    for index, label in enumerate(labels):
        if label != -1:
            cluster_groups.setdefault(int(label), []).append(index)
    result["embeddings_generated"] = len(embeddings)
    result["clusters_detected_before_quality_gates"] = len(cluster_groups)

    for label, indices in sorted(cluster_groups.items()):
        center = svc._normalize_vector(svc._mean_vector([embeddings[index] for index in indices]))
        support_values = [svc._dot(embeddings[index], center) for index in indices]
        support = round(sum(support_values) / len(support_values), 4)
        min_support = round(min(support_values), 4)
        cluster_cohesion = round((support + min_support) / 2, 4)
        sorted_indices = sorted(indices, key=lambda idx: svc._dot(embeddings[idx], center), reverse=True)
        questions = [svc.clean_question_evidence(user_texts[index]) for index in sorted_indices]
        answers = [
            svc.clean_answer_evidence(valid_items[index].get("assistant_text", ""), valid_items[index].get("company_name"))
            for index in sorted_indices
        ]
        generation: Dict[str, Any] = {}
        validation = {"accepted": None, "reason": "llm_not_run"}
        if run_llm:
            generation = svc.generate_faq_candidate_with_llm(
                company_name=valid_items[sorted_indices[0]].get("company_name"),
                questions=[user_texts[index] for index in indices],
                historical_answers=[valid_items[index].get("assistant_text", "") for index in indices],
                cluster_metrics={
                    "cluster_label": label,
                    "cluster_support": support,
                    "min_support": min_support,
                    "cluster_cohesion": cluster_cohesion,
                    "valid_question_evidence_count": len(set(questions)),
                    "valid_answer_evidence_count": sum(1 for answer in answers if svc.is_answer_evidence_usable(answer)),
                },
                recurrence=len(indices),
            )
            accepted, reason = svc.validate_generated_candidate(generation)
            validation = {"accepted": accepted, "reason": reason}

        result["clusters"].append(
            {
                "cluster_id": label,
                "cluster_size": len(indices),
                "support": support,
                "min_support": min_support,
                "cluster_cohesion": cluster_cohesion,
                "intent_categories": sorted(svc.cluster_intent_categories(questions)),
                "mixed_intent": svc.is_mixed_intent_cluster(questions),
                "questions": questions,
                "answers": answers,
                "llm_prompt": generation.get("prompt"),
                "llm_raw_json": generation.get("raw_generation"),
                "llm_parsed": {
                    key: value
                    for key, value in generation.items()
                    if key not in {"prompt", "raw_generation"}
                },
                "validation": validation,
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug FAQ pipeline for one workspace and time range.")
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--since-days", type=int, required=True)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limite explicito de mensajes. Si se omite, aplica complete_if_fits.",
    )
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    records, ingest_metrics = fetch_conversation_records_with_metrics(
        limit=args.limit,
        since_days=args.since_days,
        workspace_id=args.workspace_id,
    )
    payload = {
        "workspace_id": args.workspace_id,
        "since_days": args.since_days,
        "limit": args.limit,
        "ingest_metrics": ingest_metrics,
        "pipeline_debug": cluster_debug(records, run_llm=not args.no_llm),
    }
    output = args.output or Path("data") / f"debug_workspace_{args.workspace_id}_{args.since_days}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    summary = {
        "workspace_id": args.workspace_id,
        "since_days": args.since_days,
        "raw_messages_found": ingest_metrics.get("raw_messages_found"),
        "raw_messages_processed": ingest_metrics.get("raw_messages_processed"),
        "conversations_processed": ingest_metrics.get("conversations_processed"),
        "candidate_questions_kept_for_embedding": payload["pipeline_debug"].get("candidate_questions_kept_for_embedding"),
        "clusters_detected_before_quality_gates": payload["pipeline_debug"].get("clusters_detected_before_quality_gates", 0),
        "output": str(output),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
