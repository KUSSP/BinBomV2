# BinBomV2 runtime environment.
#   - With a binary:  analyzes it and writes a CycloneDX SBOM to /out
#   - With a command: runs that command inside /app (e.g. the DB build steps)
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

# The font libraries are needed by Ghidra's analyzers even in headless mode.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jdk-headless \
        fontconfig \
        fonts-dejavu-core \
        libfreetype6 \
        libharfbuzz0b \
        universal-ctags \
        clang \
        build-essential \
        cmake \
        git \
        file \
        ca-certificates \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python

WORKDIR /app

# Ghidra must live under tools/ghidra/ (same layout as tools/external_tools_install.sh).
ARG GHIDRA_URL=https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_11.2.1_build/ghidra_11.2.1_PUBLIC_20241105.zip
RUN mkdir -p tools/ghidra/ghidra_project \
    && curl -fsSL -o /tmp/ghidra.zip "${GHIDRA_URL}" \
    && unzip -q /tmp/ghidra.zip -d tools/ghidra \
    && rm /tmp/ghidra.zip

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen

COPY . .

# py-bsim is a git submodule used by the optional binary DB step (src/binaryDB_*.py).
RUN test -f py-bsim/py_bsim.py \
    || { echo "py-bsim submodule is missing; run 'git submodule update --init' before building." >&2; exit 1; }

ENV PATH="/app/.venv/bin:${PATH}"

RUN mkdir -p testset/bin output database binaryDB/db_input /input /out \
    && chmod +x docker-entrypoint.sh
VOLUME ["/app/database", "/app/osscollector", "/app/binaryDB", "/input", "/out"]

ENTRYPOINT ["/app/docker-entrypoint.sh"]
