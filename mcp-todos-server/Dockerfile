FROM python:3.14-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# shared/ is referenced as path = "../shared" in pyproject.toml.
# WORKDIR is /app, so ../shared resolves to /shared — copy it there.
COPY shared/ /shared/

# Install dependencies first so this layer is cached across app-code changes.
COPY mcp-todos-server/pyproject.toml mcp-todos-server/uv.lock ./
RUN uv sync --locked --no-dev

COPY mcp-todos-server/app ./app

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 9000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
