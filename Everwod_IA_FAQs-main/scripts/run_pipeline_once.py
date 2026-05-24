import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from faq_models import IngestRequest
from suggestion_service import run_suggestion_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FAQ suggestion pipeline once and persist a JSON summary.")
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--since-days", type=int, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    summary = run_suggestion_pipeline(
        IngestRequest(
            workspace_id=args.workspace_id,
            since_days=args.since_days,
            limit=args.limit,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "workspace_id": args.workspace_id,
                "since_days": args.since_days,
                "run_id": summary.run_id,
                "cluster_count": summary.cluster_count,
                "hard_rejected": summary.hard_rejected,
                "needs_review": summary.persisted_needs_review,
                "high_confidence": summary.persisted_high_confidence,
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
