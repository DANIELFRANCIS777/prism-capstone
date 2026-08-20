# Build context is the repo root (see docker-compose.yml), not backend/, because
# app/config.py locates data/ as a sibling of backend/ via a relative path -
# preserving that same layout inside the image keeps config.py unchanged.

FROM python:3.13-slim

WORKDIR /srv/prism-capstone

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY data/ data/

# Run as an unprivileged user: a container escape or a path-traversal bug
# shouldn't land root. Needs to own backend/keys because local/Compose runs
# generate key material there on first start (production supplies it via
# env instead - see app/jwt_keys.py).
RUN useradd --create-home --uid 10001 prism \
    && mkdir -p /srv/prism-capstone/backend/keys \
    && chown -R prism:prism /srv/prism-capstone

WORKDIR /srv/prism-capstone/backend
USER prism

EXPOSE 8080

# $PORT is what Render/Cloud Run/Heroku-likes inject; 8080 keeps Compose and
# local runs working unchanged.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
