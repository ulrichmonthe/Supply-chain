.PHONY: install dev backend frontend build test clean

VENV := .venv
PY   := $(VENV)/bin/python

install:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -r backend/requirements.txt
	cd frontend && npm install

## Run the API with autoreload. Pair with `make frontend` in a second terminal.
backend:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload

frontend:
	cd frontend && npm run dev

## Single-process build: the API serves the compiled frontend on :8000.
build:
	cd frontend && npm run build

dev: build
	cd backend && ../$(PY) -m uvicorn app.main:app

test:
	cd backend && ../$(PY) -m pytest -q

clean:
	rm -rf backend/var frontend/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
