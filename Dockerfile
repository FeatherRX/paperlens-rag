FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system paperlens \
    && useradd --system --gid paperlens --create-home paperlens

COPY requirements.txt ./
RUN python -m pip install --requirement requirements.txt

COPY --chown=paperlens:paperlens app ./app
RUN mkdir -p /app/data/ingested /tmp/fastembed_cache \
    && chown -R paperlens:paperlens /app/data /tmp/fastembed_cache

USER paperlens

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
