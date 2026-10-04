# Culprit — common tasks.  `make help` lists them.
.DEFAULT_GOAL := help
.PHONY: help install demo dev-backend dev-frontend test test-backend test-frontend check

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## install backend + frontend dependencies
	cd backend && pip install -r requirements.txt
	cd frontend && npm install && ( [ -f .env.local ] || cp .env.example .env.local )

dev-backend:  ## run the API in OFFLINE mode (no API keys; benchmarks are real)
	cd backend && CULPRIT_OFFLINE=1 python api.py

dev-frontend:  ## run the dashboard on http://localhost:3000
	cd frontend && npm run dev

test-backend:  ## backend tests (real git repos + sandbox subprocesses)
	cd backend && python -m pytest -q

test-frontend:  ## frontend unit tests, typecheck, lint
	cd frontend && npm test && npm run typecheck && npm run lint

test: test-backend test-frontend  ## everything

check: test  ## tests + production build
	cd frontend && npm run build
