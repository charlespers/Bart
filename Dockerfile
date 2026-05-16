# bart website — single-container image for Fly.io / any Docker host.
#
# Builds two python venvs (bart's own deps + the website's), installs the
# claude CLI globally via npm, and runs uvicorn on :8080. State (sqlite db +
# uploaded materials + generated packets + per-user claude credentials) is
# routed onto a /data volume by entrypoint.sh so it survives redeploys.

FROM python:3.11-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# system deps: node (for the claude CLI), sqlite (handy for ops), curl/ca
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg sqlite3 \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# claude CLI — what the website spawns per-user for `claude auth login`
# and for the bart pipeline itself.
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

# bart's Python deps — server.py expects this venv at /app/.venv
COPY requirements.txt ./
RUN python -m venv .venv \
 && .venv/bin/pip install --upgrade pip wheel \
 && .venv/bin/pip install -r requirements.txt

# website's deps in its own venv (dev.sh expects this layout)
COPY website/requirements.txt ./website/
RUN python -m venv website/.venv \
 && website/.venv/bin/pip install --upgrade pip wheel \
 && website/.venv/bin/pip install -r website/requirements.txt

# project source (after deps so dep layer caches between rebuilds)
COPY . .

# entrypoint symlinks state onto /data and starts uvicorn
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
