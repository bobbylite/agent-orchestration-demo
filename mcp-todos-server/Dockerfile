FROM python:3.14-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY server.py ./

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 9000

CMD ["python", "server.py"]
