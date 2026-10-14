# ULIS backend - production image (multi-stage, non-root, no secrets).
#
# Build context is the repository root:  docker build -f deploy/Dockerfile .
# (deploy/docker-compose.yml does this for you.)
#
# The application is deliberately NOT pip-installed into site-packages: ulis.settings.REPO_ROOT is the
# directory above the package, and it must hold config/, migrations/ and alembic.ini. Running the code
# from /app keeps that true. Only the third-party dependencies are installed (into /opt/venv).
# NOTE: written without a Docker daemon available; see docs/deployment.md ("What was tested").
ARG PYTHON_VERSION=3.13

# ---- build stage: dependencies, and a staging copy of the files the image needs -------------------
FROM python:${PYTHON_VERSION}-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY pyproject.toml ./
RUN python -c "import tomllib; print(*tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies'], sep='\n')" > requirements.txt \
    && pip install -r requirements.txt
# Stage the application files (the build context is filtered by .dockerignore: no var/, .git, .env, tests).
COPY . /src
# eval/ may or may not exist in this checkout; make sure the COPY below always has a source.
RUN mkdir -p /src/eval

# ---- runtime stage ----------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    HOME=/home/ulis \
    ULIS_ENV=production \
    ULIS_OBJECT_STORE_DIR=/data/objects \
    ULIS_SOURCE_CACHE_DIR=/data/sources
# git: the connectors for Canada and the US GPO samples clone pinned commits.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /home/ulis --shell /usr/sbin/nologin ulis \
    && mkdir -p /data/objects /data/sources \
    && chown -R ulis:ulis /data /home/ulis
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --from=build /src/ulis ./ulis
COPY --from=build /src/config ./config
COPY --from=build /src/migrations ./migrations
COPY --from=build /src/alembic.ini ./alembic.ini
COPY --from=build /src/eval ./eval
COPY --from=build /src/sample_corpus ./sample_corpus
USER ulis
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
# Applies migrations and syncs the catalog, then serves. docker-compose.yml overrides this with the same
# steps; running the image directly needs ULIS_API_KEYS, ULIS_DATABASE_URL (a non-default password) and so on.
CMD ["sh", "-c", "python -m ulis.cli migrate && python -m ulis.cli sync-catalog && exec uvicorn ulis.api.app:create_app --factory --host 0.0.0.0 --port 8000 --workers 1 --no-access-log --no-server-header"]
