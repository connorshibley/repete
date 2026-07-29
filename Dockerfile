# Trading agent — always-on container image (2026-07-22).
# The laptop/launchd setup cannot back a subscription product: a closed lid
# is a missed trading day. This image runs the same scripts, unattended.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/New_York

WORKDIR /app

# Dependencies first — they change far less often than the source.
#
# The LOCKFILE, not requirements.txt (W4-1, 2026-07-29). requirements.txt names
# the direct dependencies; requirements.lock pins all 58 including the
# transitive closure. numpy and pandas arrive through that closure — nothing
# names them — and they are exactly the packages whose float behaviour a gate
# verdict rests on. Installing the loose file here let the container and the
# laptop resolve differently on any given day, which is the one thing a project
# built on reproducible verdicts cannot afford.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY config.yaml ./
COPY knowledge/ ./knowledge/

# WHICH BUILD IS THIS? The image ships without .git, so src/deploycheck.py
# cannot ask git what is running — and "unknown" is deliberately not an alert
# (a daily false alarm mutes the channel). Bake the sha at build time:
#
#   docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t repete .
#
# Skip it and the drift guard degrades to config-drift only. That still catches
# the case that actually happened (§26 divergence #7 — a running config.yaml
# that was not the gated one), but the ledger stamp on every cycle_complete
# stops telling you which code traded.
ARG GIT_SHA=""
ENV REPETE_GIT_SHA=$GIT_SHA

# Non-root: the agent never needs privileges. memory/ and logs/ are volumes.
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p memory logs \
    && chown -R agent:agent /app
USER agent

# Secrets arrive as environment variables (ALPACA_API_KEY, ANTHROPIC_API_KEY,
# X_*, …) — never baked into the image, never a committed .env.

HEALTHCHECK --interval=5m --timeout=30s --start-period=1m --retries=2 \
  CMD python src/health.py || exit 1

# Default: the scheduler loop. Override to run one cycle:
#   docker run … python src/main.py
CMD ["python", "scripts/scheduler.py"]
