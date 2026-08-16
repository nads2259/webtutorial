SHELL := /bin/bash
PG_CONTAINER := webtutorial-pg
DATABASE_URL ?= postgresql+psycopg://postgres:postgres@localhost:5432/webtutorial

.PHONY: help db-up db-down venv install bootstrap api web

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n",$$1,$$2}'

db-up: ## Start local Postgres 18 + pgvector
	docker run -d --name $(PG_CONTAINER) -e POSTGRES_PASSWORD=postgres -p 5432:5432 pgvector/pgvector:pg18

db-down: ## Stop and remove the local Postgres container
	-docker stop $(PG_CONTAINER) && docker rm $(PG_CONTAINER)

venv: ## Create .venv (Python 3.13) and install the runtime
	python3.13 -m venv .venv && . .venv/bin/activate && pip install -e platform/python -r requirements-dev.txt

install: ## Install web workspace deps
	pnpm install

bootstrap: ## One-touch: doctor -> migrate (alembic upgrade head) -> seed -> smoke
	DATABASE_URL="$(DATABASE_URL)" ./northstar bootstrap --profile ci

api: ## Run the backend API (uvicorn -> northstar.processes.api.asgi:app)
	. .venv/bin/activate && DATABASE_URL="$(DATABASE_URL)" python -m northstar.processes.api

web: ## Run the learner web UI
	pnpm --filter @northstar/web dev
