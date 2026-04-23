# Railway / Nixpacks: repo root `main.py` is the legacy CLI scanner, not FastAPI.
# Migrate from repo root (alembic.ini), then run the API from backend/ (package `app`).
web: sh -c "python -m alembic upgrade head && cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT"
