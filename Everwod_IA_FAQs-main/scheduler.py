"""Legacy entrypoint for the monthly FAQ scheduler."""

from app.jobs.scheduler import main, scheduled_pipeline


if __name__ == "__main__":
    main()
