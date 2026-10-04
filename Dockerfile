FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend ./backend
COPY dist ./dist
RUN DJANGO_DEBUG=1 uv run --no-sync backend/manage.py collectstatic --noinput
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["/app/.venv/bin/gunicorn", "--chdir", "backend", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "45", "--access-logfile", "-", "--error-logfile", "-"]
