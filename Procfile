api:    uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
worker: celery -A src.celery_app worker --loglevel=info --concurrency=4 --queues=collectors,analysis,signals
beat:   celery -A src.celery_app beat --loglevel=info
