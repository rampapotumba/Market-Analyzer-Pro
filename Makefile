.PHONY: install install-dev run test lint format migrate seed clean docker-up docker-down

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

# ── Database ──────────────────────────────────────────
migrate:
	alembic upgrade head

migrate-new:
	alembic revision --autogenerate -m "$(msg)"

seed:
	python -m src.database.seed

# ── Test ──────────────────────────────────────────────
test:
	pytest

test-cov:
	pytest --cov=src --cov-report=html

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

docker-logs:
	docker-compose logs -f app

# ── Utilities ─────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .mypy_cache .ruff_cache

collect:
	python -m src.collectors.run_all

analyze:
	python -m src.signals.run_analysis $(symbol) $(tf)
