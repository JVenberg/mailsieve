FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY mailsieve ./mailsieve
CMD ["uv", "run", "--no-dev", "gunicorn", "--bind", ":8080", "--workers", "1", "--timeout", "900", "mailsieve.app:app"]
