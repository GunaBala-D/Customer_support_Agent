PY ?= backend/.venv/bin/python
PYTHON ?= python3   # on a Mac with several Pythons: make install PYTHON=python3.12
# On Windows (Git Bash) use:  make PY=backend/.venv/Scripts/python <target>

.PHONY: install dev dev-backend dev-frontend test seed evaluate lint models docker-up docker-down

install:
	$(PYTHON) -m venv backend/.venv
	$(PY) -m pip install -r backend/requirements.txt
	cd frontend && npm ci
	@test -f .env || cp .env.example .env

dev:
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

test:
	cd backend && ../$(PY) -m pytest -q

seed:
	$(PY) scripts/seed.py

evaluate:
	$(PY) -m evaluation.run

lint:
	$(PY) -m ruff check backend evaluation scripts
	cd frontend && npm run lint

models:
	ollama pull $${OLLAMA_MODEL:-llama3.2:3b}
	ollama pull $${EMBEDDING_MODEL:-nomic-embed-text}

docker-up:
	docker compose up --build

docker-down:
	docker compose down
