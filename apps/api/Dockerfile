FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Prefer pre-vendored wheels (offline). If directory empty — fall back to PyPI (online build once).
COPY vendor/python-wheels /wheels
RUN pip install --upgrade pip \
    && if ls /wheels/*.whl >/dev/null 2>&1; then \
         echo "[deps] offline install from /wheels"; \
         pip install --no-index --find-links=/wheels -r requirements.txt; \
       else \
         echo "[deps] WARNING: vendor/python-wheels empty — installing from network (run scripts/vendor-python-wheels.sh)"; \
         pip install -r requirements.txt; \
       fi

COPY pyproject.toml README.md ./
COPY apps ./apps
COPY modules ./modules
COPY rag ./rag

EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
