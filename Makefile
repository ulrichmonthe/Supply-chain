.PHONY: help install run dev backend frontend build test clean check-python

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

# Python 3.9 or newer. 3.9 is what macOS ships as the system python3, and the code is
# written to run on it: no PEP 604 unions in evaluated positions, no dataclass slots.
PYTHON ?= $(shell command -v python3)

# Override if something already owns 8000:  make run PORT=8080
PORT ?= 8000

help:
	@echo "make install   create the virtualenv and install dependencies"
	@echo "make run       run the app on http://localhost:8000  (no Node required)"
	@echo "               override the port with:  make run PORT=8080"
	@echo "make dev       rebuild the frontend from source, then run  (needs Node)"
	@echo "make backend   API with autoreload, for frontend development"
	@echo "make frontend  Vite dev server on :5173, proxying to the API"
	@echo "make test      run the test suite"

check-python:
	@$(PYTHON) -c 'import sys; \
	  sys.exit(0) if sys.version_info >= (3, 9) else \
	  (print(f"\nThis project needs Python 3.9 or newer; {sys.executable} is {sys.version.split()[0]}.\n"), \
	   sys.exit(1))'

install: check-python
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt
	@echo "Backend ready."
	@if command -v npm >/dev/null 2>&1; then \
	  cd frontend && npm install; \
	  echo "Frontend toolchain ready. Use 'make dev' to rebuild from source."; \
	else \
	  echo ""; \
	  echo "Node/npm was not found, which is fine: a built frontend is committed to"; \
	  echo "the repository, so 'make run' works as it is. Install Node only if you"; \
	  echo "want to change the interface."; \
	fi

## The zero-toolchain path. Serves the committed frontend build from the API.
run:
	@test -f frontend/dist/index.html || { \
	  echo "frontend/dist is missing. Run 'make build' (needs Node)."; exit 1; }
	@echo ""
	@echo "  Starting the server. Open http://localhost:$(PORT) in your browser."
	@echo "  The first start takes a few seconds: it seeds the dataset and solves the baseline."
	@echo "  Leave this running; press Ctrl+C to stop."
	@echo ""
	cd backend && ../$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port $(PORT)

build:
	@command -v npm >/dev/null 2>&1 || { \
	  echo "npm not found. 'make run' uses the committed build and needs no Node."; exit 1; }
	cd frontend && npm install && npm run build

dev: build run

backend:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload

frontend:
	cd frontend && npm run dev

test:
	cd backend && ../$(PY) -m pytest -q

clean:
	rm -rf backend/var
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
