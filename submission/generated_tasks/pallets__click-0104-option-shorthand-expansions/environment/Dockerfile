# Harbor environment for pallets__click-0104-option-shorthand-expansions: pallets/click at 8.5.1 (cf19179e8e2fd54c601378cec9e0d21bb12b6fb1).
# The source is fetched at the pinned commit and re-committed as a fresh one-commit git tree, so
# solve.sh and the grader can `git apply` and no later upstream history is visible in /app.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/pallets/click.git \
    && git -C /app fetch -q --depth 1 origin cf19179e8e2fd54c601378cec9e0d21bb12b6fb1 \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git \
    && git init -q /app \
    && git -C /app config user.email bench@local && git -C /app config user.name bench \
    && git -C /app config core.autocrlf false \
    && git -C /app add -A && git -C /app commit -q -m "base state" \
    && git config --global --add safe.directory /app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -e /app "pytest==8.4.2" \
    && /opt/venv/bin/python -c "import click, sys; print('click', click.__file__); sys.exit(0 if click.__file__.startswith('/app/') else 1)"
ENV PATH="/opt/venv/bin:$PATH"
CMD ["/bin/bash"]
