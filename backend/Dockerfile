# Build context is the repo root (see docker-compose.yml), not backend/, because
# app/config.py locates data/ as a sibling of backend/ via a relative path -
# preserving that same layout inside the image keeps config.py unchanged.

FROM python:3.13-slim

WORKDIR /srv/prism-capstone

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY data/ data/

WORKDIR /srv/prism-capstone/backend

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
