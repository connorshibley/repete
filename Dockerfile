# Trading agent — always-on container image (2026-07-22).
# The laptop/launchd setup cannot back a subscription product: a closed lid
# is a missed trading day. This image runs the same scripts, unattended.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/New_York

WORKDIR /app

# scripts/publish_dashboard.sh runs `git -C .site add/commit/push` from
# INSIDE this container against the host-mounted .site/ checkout (see
# docker-compose.yml's .site mount) — the base image ships without git at
# all, which made every publish attempt fail with "git: not found" rather
# than the intended "no .site/.git, clean no-op". Installed once, near the
# top, since it changes essentially never and this keeps the layer cached
# across nearly every rebuild.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

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
#
# HOST_UID/HOST_GID (default 10001, the original fixed value) let a host
# bake the agent user to match its own uid at build time -- deploy.sh
# exports these from `id -u`/`id -g`. Needed for deploy_key (docker-
# compose.yml): a bind-mounted private key has to stay mode 600, which
# only the matching uid can read, so "run as some arbitrary fixed uid" and
# "read a 600 host file" are only both possible if the container's user
# uid equals the host owner's. A host that doesn't mount a deploy_key
# never sets these and gets uid 10001 exactly as before.
ARG HOST_UID=10001
ARG HOST_GID=10001
RUN groupadd -g "${HOST_GID}" agent \
    && useradd --create-home --uid "${HOST_UID}" --gid "${HOST_GID}" agent \
    && mkdir -p memory logs \
    && chown -R agent:agent /app
USER agent

# git (installed above) refuses to operate on a repo whose ON-DISK owner
# doesn't match the running UID (its "dubious ownership" check, CVE-2022-
# 24765) — which is exactly what .site/ is: host-mounted, owned by whatever
# user cloned it there, read/written by this container's uid 10001. That
# check exists to stop an attacker-controlled repo path from being trusted
# silently; /app/.site is a path WE mount on purpose (docker-compose.yml),
# so declaring it safe here is scoping the exception to the one directory
# that earns it, not disabling the check globally.
RUN git config --global --add safe.directory /app/.site

# Secrets arrive as environment variables (ALPACA_API_KEY, ANTHROPIC_API_KEY,
# X_*, …) — never baked into the image, never a committed .env.

HEALTHCHECK --interval=5m --timeout=30s --start-period=1m --retries=2 \
  CMD python src/health.py || exit 1

# Default: the scheduler loop. Override to run one cycle:
#   docker run … python src/main.py
CMD ["python", "scripts/scheduler.py"]
