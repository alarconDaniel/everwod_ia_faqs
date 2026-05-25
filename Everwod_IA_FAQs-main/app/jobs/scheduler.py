from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from app.core.common import FAQ_INGEST_SOURCE, FAQ_RAW_SCHEMA, FAQ_SCHEMA
from app.core.config import FAQ_MONTHLY_RUN_INGEST, FAQ_SCHEDULE_INTERVAL_DAYS, FAQ_SCHEDULE_SINCE_DAYS
from app.core.models import IngestRequest
from app.ingestion.normalized_writer import run_everwod_ingestion
from app.pipeline.pipeline import run_suggestion_pipeline


def scheduled_pipeline() -> None:
    start = datetime.utcnow()
    print(f"[{start.isoformat()}] Iniciando pipeline mensual de FAQ automatica...")
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
        print(
            "  - Ingesta raw ejecutada: "
            f"source={FAQ_INGEST_SOURCE}, records_written={ingest_metrics.get('records_written', 0)}"
        )

    request = IngestRequest(
        limit=None,
        since_days=FAQ_SCHEDULE_SINCE_DAYS,
        workspace_id=None,
        agent_id=None,
    )

    summary = run_suggestion_pipeline(request)

    print(f"  - Pipeline run: {summary.run_id}")
    print(f"  - Empresas analizadas: {summary.company_count}")
    print(f"  - Candidatos generados/actualizados: {summary.cluster_count}")
    print(f"  - Ejemplos candidatos: {summary.total_examples}")
    print(f"  - Silhouette: {summary.silhouette_score}")
    print(f"[{datetime.utcnow().isoformat()}] Pipeline completado.")


def main() -> None:
    scheduler = BlockingScheduler()
    scheduler.add_job(
        scheduled_pipeline,
        "interval",
        days=FAQ_SCHEDULE_INTERVAL_DAYS,
        next_run_time=datetime.now(),
    )

    print(f"Scheduler iniciado: el job se ejecutara cada {FAQ_SCHEDULE_INTERVAL_DAYS} dias.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler detenido manualmente.")


if __name__ == "__main__":
    main()
