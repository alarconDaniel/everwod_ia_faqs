from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from faq_common import get_int_env
from faq_models import IngestRequest
from suggestion_service import run_suggestion_pipeline


FAQ_SCHEDULE_INTERVAL_DAYS = get_int_env("FAQ_SCHEDULE_INTERVAL_DAYS", 7)
FAQ_SCHEDULE_LIMIT = get_int_env("FAQ_SCHEDULE_LIMIT", 15000)
FAQ_SCHEDULE_SINCE_DAYS = get_int_env("FAQ_SCHEDULE_SINCE_DAYS", 90)


def scheduled_pipeline() -> None:
    start = datetime.utcnow()
    print(f"[{start.isoformat()}] Iniciando pipeline semanal de FAQ automatica...")
    request = IngestRequest(limit=FAQ_SCHEDULE_LIMIT, since_days=FAQ_SCHEDULE_SINCE_DAYS)
    summary = run_suggestion_pipeline(request)
    print(f"  - Pipeline run: {summary.run_id}")
    print(f"  - Empresas analizadas: {summary.company_count}")
    print(f"  - Candidatos generados/actualizados: {summary.cluster_count}")
    print(f"  - Ejemplos candidatos: {summary.total_examples}")
    print(f"  - Silhouette: {summary.silhouette_score}")
    print(f"[{datetime.utcnow().isoformat()}] Pipeline completado.")


if __name__ == "__main__":
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
