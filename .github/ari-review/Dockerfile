# Build ONLY from trusted workflow/base source, never the candidate checkout.
FROM python:3.12.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.28
WORKDIR /app
COPY . /app
RUN uv sync --frozen && chmod -R a+rX /app
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
