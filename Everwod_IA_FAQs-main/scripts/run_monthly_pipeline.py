from faq_models import IngestRequest
from suggestion_service import run_suggestion_pipeline


def main() -> None:
    request = IngestRequest(
        limit=None,
        since_days=365,
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