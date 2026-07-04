.PHONY: help up down migrate test lint format clean

help:
	@echo "Available commands:"
	@echo "  make up       - Start services with docker-compose"
	@echo "  make down     - Stop all services"
	@echo "  make migrate  - Run database migrations"
	@echo "  make test     - Run tests"
	@echo "  make lint     - Run linters (ruff, black, mypy)"
	@echo "  make format   - Format code with black and ruff"
	@echo "  make install  - Install dependencies"
	@echo "  make clean    - Clean cache and logs"

install:
	pip install -r requirements.txt

up:
	docker-compose up -d
	@echo "Services started. Bot running on http://localhost:8000"

down:
	docker-compose down

logs:
	docker-compose logs -f bot

migrate:
	docker-compose exec bot python -m alembic upgrade head

test:
	pytest tests/ -v --cov=bot --cov-report=term-missing

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
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .coverage htmlcov
	rm -rf logs/*.log
