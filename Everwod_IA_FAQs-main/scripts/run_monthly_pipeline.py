import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.common import FAQ_INGEST_SOURCE, FAQ_RAW_SCHEMA, FAQ_SCHEMA
from app.core.config import FAQ_MONTHLY_RUN_INGEST, FAQ_SCHEDULE_SINCE_DAYS
from app.core.models import IngestRequest
from app.ingestion.normalized_writer import run_everwod_ingestion
from app.pipeline.pipeline import run_suggestion_pipeline


def main() -> None:
    if FAQ_MONTHLY_RUN_INGEST:
        ingest_metrics = run_everwod_ingestion(
            limit=None,
            since_days=FAQ_SCHEDULE_SINCE_DAYS,
            workspace_id=None,
            agent_id=None,
            dry_run=False,
            raw_schema=FAQ_RAW_SCHEMA,
            target_schema=FAQ_SCHEMA,
        )
        print("Monthly raw ingestion finished.")
        print(f"source={FAQ_INGEST_SOURCE}")
        print(f"records_written={ingest_metrics.get('records_written', 0)}")

    request = IngestRequest(
        limit=None,
        since_days=FAQ_SCHEDULE_SINCE_DAYS,
        workspace_id=None,
        agent_id=None,
    )

    summary = run_suggestion_pipeline(request)

    print("Monthly FAQ pipeline finished.")
    print(f"run_id={summary.run_id}")
    print(f"company_count={summary.company_count}")
    print(f"suggestions={summary.cluster_count}")
    print(f"examples={summary.total_examples}")


if __name__ == "__main__":
    main()
