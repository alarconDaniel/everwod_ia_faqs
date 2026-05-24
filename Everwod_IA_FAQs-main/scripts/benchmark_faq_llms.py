import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import suggestion_service as svc
from debug_workspace_pipeline import cluster_debug
from ingest_service import fetch_conversation_records_with_metrics


DEFAULT_MODELS = [
    "Qwen/Qwen2.5-0.5B-Instruct",
    "Qwen/Qwen3-1.7B",
    "Qwen/Qwen2.5-1.5B-Instruct",
]


def reset_llm(model_name: str) -> None:
    svc.FAQ_LLM_MODEL = model_name
    svc.FAQ_LLM_ENABLED = True
    svc.MODELS_READY = False
    svc.ANSWER_GENERATOR_READY = False
    svc.ANSWER_GENERATOR = None
    svc.EMBEDDING_MODEL = svc.EMBEDDING_MODEL
    svc.load_models()


def run_for_model(model_name: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        reset_llm(model_name)
        if not svc.ANSWER_GENERATOR:
            return {
                "model": model_name,
                "load_or_generation_error": "model_unavailable",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "clusters": [],
            }
        debug = cluster_debug(records, run_llm=True)
        rows = []
        for cluster in debug.get("clusters", []):
            parsed = cluster.get("llm_parsed") or {}
            question = parsed.get("canonical_question") or ""
            answer = parsed.get("canonical_answer") or ""
            knowledge = parsed.get("knowledge_statement") or ""
            aligned, alignment_reason = svc.is_question_answer_aligned(question, answer, knowledge)
            rows.append(
                {
                    "cluster_id": cluster.get("cluster_id"),
                    "cluster_size": cluster.get("cluster_size"),
                    "model": model_name,
                    "question": question,
                    "answer": answer,
                    "knowledge_statement": knowledge,
                    "publish": parsed.get("publish"),
                    "confidence": parsed.get("confidence"),
                    "valid_json": bool(parsed),
                    "question_answer_aligned": aligned,
                    "alignment_reason": alignment_reason,
                    "contains_thinking_text": "<think>" in (cluster.get("llm_raw_json") or "").lower(),
                    "question_words": svc.word_count(question),
                    "answer_words": svc.word_count(answer),
                    "generation_elapsed_ms": parsed.get("generation_elapsed_ms"),
                    "validation": cluster.get("validation"),
                }
            )
        return {
            "model": model_name,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "clusters": rows,
        }
    except Exception as exc:
        return {
            "model": model_name,
            "load_or_generation_error": str(exc),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "clusters": [],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local FAQ LLMs over the same workspace clusters.")
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--since-days", type=int, required=True)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limite explicito de mensajes. Si se omite, aplica complete_if_fits.",
    )
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    records, ingest_metrics = fetch_conversation_records_with_metrics(
        limit=args.limit,
        since_days=args.since_days,
        workspace_id=args.workspace_id,
    )
    payload: Dict[str, Any] = {
        "workspace_id": args.workspace_id,
        "since_days": args.since_days,
        "limit": args.limit,
        "ingest_metrics": ingest_metrics,
        "models": [],
    }
    for model_name in args.models:
        payload["models"].append(run_for_model(model_name, records))

    output = args.output or Path("data") / f"benchmark_faq_llms_{args.workspace_id}_{args.since_days}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    summary = {
        "workspace_id": args.workspace_id,
        "since_days": args.since_days,
        "output": str(output),
        "models": [
            {
                "model": item["model"],
                "clusters": len(item.get("clusters", [])),
                "error": item.get("load_or_generation_error"),
                "elapsed_seconds": item.get("elapsed_seconds"),
            }
            for item in payload["models"]
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
