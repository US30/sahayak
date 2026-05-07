.PHONY: install backend dashboard fl-server flutter-run lint test eval

# ── Backend ───────────────────────────────────────────────────────────────────
install:
	pip install -r backend/requirements.txt

backend:
	cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000

backend-prod:
	cd backend && uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2

# ── Dashboard ─────────────────────────────────────────────────────────────────
dashboard:
	streamlit run dashboard/app.py

# ── Federated Learning ────────────────────────────────────────────────────────
fl-server:
	cd backend && python -c "from federation.server import start_fl_server; start_fl_server()"

# ── Flutter App ───────────────────────────────────────────────────────────────
flutter-run:
	cd app && flutter run

flutter-build:
	cd app && flutter build apk --release

flutter-install:
	cd app && flutter pub get

# ── Quality ───────────────────────────────────────────────────────────────────
lint:
	cd backend && python -m ruff check . --fix

test:
	cd backend && python -m pytest tests/ -v

# ── Evaluation ────────────────────────────────────────────────────────────────
eval:
	curl -s -X POST http://localhost:8000/eval/run \
	  -H "Content-Type: application/json" \
	  -d '{"user_id": "eval_user"}' | python -m json.tool

# ── Setup ─────────────────────────────────────────────────────────────────────
setup:
	cp backend/.env.example backend/.env
	mkdir -p backend/data/lancedb backend/data/faces
	@echo "Edit backend/.env to add ANTHROPIC_API_KEY, then run: make backend"

# ── All (dev) ─────────────────────────────────────────────────────────────────
dev:
	@echo "Start these in separate terminals:"
	@echo "  make backend"
	@echo "  make dashboard"
	@echo "  make flutter-run"
