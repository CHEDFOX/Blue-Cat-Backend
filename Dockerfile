FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY ulis ./ulis
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
COPY sample_corpus ./sample_corpus
RUN useradd --system --uid 10001 ulis && mkdir -p /data/objects && chown ulis /data/objects
USER ulis
EXPOSE 8000
CMD ["sh", "-c", "python -m ulis.cli migrate && python -m ulis.cli ingest ulis-fictional-sample && uvicorn ulis.api.app:create_app --factory --host 0.0.0.0 --port 8000"]
