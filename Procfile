release: alembic upgrade head
web: uvicorn clinicflow.main:app --host 0.0.0.0 --port $PORT
worker: python -m clinicflow.worker
