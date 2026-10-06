# Supply actual digest-pinned Linux images; no floating/default image is selected here.
ARG PYTHON_BASE_IMAGE
ARG UV_BINARY_IMAGE
FROM ${UV_BINARY_IMAGE} AS uv_binary
FROM ${PYTHON_BASE_IMAGE} AS dependencies

COPY --from=uv_binary /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/bf-venv \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
ARG BF_UV_LOCK_SHA256
RUN python -c "import hashlib,pathlib,sys; assert sys.version_info[:2]==(3,12); assert hashlib.sha256(pathlib.Path('uv.lock').read_bytes()).hexdigest()==sys.argv[1]" "$BF_UV_LOCK_SHA256"
RUN uv --version && uv sync --frozen --no-dev --no-install-project --no-editable --python /usr/local/bin/python
RUN /opt/bf-venv/bin/python -c "import fastapi,uvicorn,pydantic,sqlalchemy,alembic,psycopg; from zoneinfo import ZoneInfo; assert ZoneInfo('Asia/Shanghai').key=='Asia/Shanghai'"

# Both stages use the exact same base: the Linux venv's interpreter/ABI stays compatible.
FROM ${PYTHON_BASE_IMAGE} AS runtime
ENV PATH=/opt/bf-venv/bin:$PATH \
    PYTHONPATH=/workspace/apps/api \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC
WORKDIR /workspace
COPY --from=dependencies /opt/bf-venv /opt/bf-venv
COPY pyproject.toml uv.lock alembic.ini ./
COPY apps/api/ ./apps/api/
COPY scripts/scenario_runner_rpc.py scripts/seed_demo.py ./scripts/
COPY deploy/initialize_demo.py ./deploy/initialize_demo.py
ARG BF_SOURCE_HEAD
ARG BF_SOURCE_DIGEST
ARG BF_UV_LOCK_SHA256
LABEL org.opencontainers.image.revision=$BF_SOURCE_HEAD \
      io.bounded-funds.source-digest=$BF_SOURCE_DIGEST \
      io.bounded-funds.uv-lock-sha256=$BF_UV_LOCK_SHA256 \
      io.bounded-funds.purpose=ISOLATED_SIMULATED_DEMO
RUN python -c "import re,sys; assert re.fullmatch('[0-9a-f]{40}',sys.argv[1]); assert re.fullmatch('[0-9a-f]{64}',sys.argv[2]); from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai')" "$BF_SOURCE_HEAD" "$BF_SOURCE_DIGEST"
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
