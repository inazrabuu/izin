FROM node:22-alpine AS inbox
WORKDIR /inbox
COPY inbox/package.json inbox/package-lock.json ./
RUN npm ci
COPY inbox/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \ 
    PYTHONUNBUFFERED=1 \ 
    UV_COMPILE_BYTECODE=1 \ 
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
COPY sdk/ /sdk/
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY server/ ./
RUN uv sync --locked --no-dev 

COPY --from=inbox /inbox/dist ./static

ENV PATH="/app/.venv/bin:$PATH" \
    IZIN_STATIC_DIR=/app/static

RUN useradd --create-home --uid 10001 izin
USER izin
EXPOSE 8000
CMD ["uvicorn", "izin_server.main:app", "--host", "0.0.0.0", "--port", "8000"]