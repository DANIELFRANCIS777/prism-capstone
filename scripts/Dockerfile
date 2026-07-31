# Zero-dependency mock provider (stdlib only) - baked into the image instead
# of bind-mounted, so this runs on any machine without Docker Desktop
# file-sharing configuration.
FROM python:3.13-slim
WORKDIR /app
COPY scripts/mock_provider.py scripts/mock_provider.py
ENTRYPOINT ["python", "scripts/mock_provider.py"]
