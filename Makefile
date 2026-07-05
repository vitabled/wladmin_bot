.PHONY: help install up down logs migrate revision test test-fast lint format clean

help:
	@echo "Available commands:"
	@echo "  make install   - Install dev dependencies into the active venv"
	@echo "  make up         - Start all services (docker compose up -d, build)"
	@echo "  make down       - Stop all services"
	@echo "  make logs       - Tail bot logs"
	@echo "  make migrate    - Apply DB migrations inside the bot container"
	@echo "  make revision m=\"msg\" - Autogenerate a new migration"
	@echo "  make test       - Run the full test suite with coverage"
	@echo "  make test-fast  - Run tests, stop on first failure"
	@echo "  make lint       - ruff + black --check + mypy (services)"
	@echo "  make format     - Auto-format with ruff --fix + black"
	@echo "  make clean      - Remove caches and logs"

install:
	pip install -r requirements-dev.txt

up:
	docker compose up -d --build
	@echo "Services started. Health: http://localhost:8000/health"

down:
	docker compose down

logs:
	docker compose logs -f bot

migrate:
	docker compose exec bot python -m alembic upgrade head

revision:
	docker compose exec bot python -m alembic revision --autogenerate -m "$(m)"

test:
	pytest tests/ --cov=bot --cov-report=term-missing

test-fast:
	pytest tests/ -x -q

lint:
	ruff check bot tests
	black --check bot tests
	mypy bot/services --strict

format:
	ruff check --fix bot tests
	black bot tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .coverage htmlcov
	rm -rf logs/*.log
