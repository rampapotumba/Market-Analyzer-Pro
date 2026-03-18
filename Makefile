.PHONY: install install-dev run dev stop test lint format migrate seed clean
.PHONY: docker-up docker-down docker-logs docker-build
.PHONY: celery-worker celery-beat celery-flower
.PHONY: collect analyze backtest

# ── Setup ─────────────────────────────────────────────
install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt
	pre-commit install

# ── Run ───────────────────────────────────────────────
run:
	uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

run-prod:
	uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4

# Start all local processes (API + Celery worker + Beat) via Procfile
dev:
	honcho start

# Stop all background processes started by dev
stop:
	@pkill -f "uvicorn src.main" 2>/dev/null || true
	@pkill -f "celery.*worker" 2>/dev/null || true
	@pkill -f "celery.*beat" 2>/dev/null || true
	@echo "All processes stopped."

# ── Celery ────────────────────────────────────────────
celery-worker:
	celery -A src.celery_app worker --loglevel=info --concurrency=4

celery-beat:
	celery -A src.celery_app beat --loglevel=info

celery-flower:
	celery -A src.celery_app flower --port=5555

# ── Database ──────────────────────────────────────────
migrate:
	alembic upgrade head

migrate-new:
	alembic revision --autogenerate -m "$(msg)"

migrate-down:
	alembic downgrade -1

seed:
	python -m src.database.seed

# ── Test ──────────────────────────────────────────────
test:
	pytest

test-cov:
	pytest --cov=src --cov-report=html --cov-report=term-missing

test-unit:
	pytest tests/ -m "not integration" -v

test-integration:
	pytest tests/ -m "integration" -v

# ── Lint ──────────────────────────────────────────────
lint:
	ruff check src/ tests/
	mypy src/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

# ── Docker ────────────────────────────────────────────
docker-up:
	docker-compose up -d --build

docker-down:
	docker-compose down

docker-down-volumes:
	docker-compose down -v

docker-logs:
	docker-compose logs -f

docker-logs-app:
	docker-compose logs -f app

docker-logs-worker:
	docker-compose logs -f celery_worker

docker-logs-finbert:
	docker-compose logs -f finbert

docker-build:
	docker-compose build --no-cache

docker-ps:
	docker-compose ps

# ── Utilities ─────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .mypy_cache .ruff_cache

collect:
	python -m src.collectors.run_all

analyze:
	python -m src.signals.run_analysis $(symbol) $(tf)

backtest:
	python -m src.backtesting.run $(symbol) $(tf)

detect-regimes:
	python -m src.analysis.regime_detector --all

# ── Quick Status ──────────────────────────────────────
status:
	@echo "=== Docker Services ==="
	@docker-compose ps
	@echo ""
	@echo "=== Health Check ==="
	@curl -s http://localhost:8000/api/v2/health | python -m json.tool 2>/dev/null || echo "App not running"
	@echo ""
	@echo "=== Redis ==="
	@redis-cli ping 2>/dev/null || echo "Redis not running"
