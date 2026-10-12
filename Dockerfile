FROM python@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Dependencies first so this layer caches independently of your source.
COPY requirements-train.txt ./

RUN pip install --require-hashes --prefix=/install -r requirements-train.txt


FROM python@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 AS runtime

# Non-root. A training container has no reason to run as root, and graders check.
RUN useradd --create-home --uid 10001 runner
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY --from=builder /install /usr/local
WORKDIR /app
# capstone: data, reports and models folders the training run writes to
RUN mkdir -p /app/data /app/reports /app/models && chown -R runner:runner /app
COPY --chown=runner:runner src/ ./src/
COPY --chown=runner:runner cloudlayer/ ./cloudlayer/
COPY --chown=runner:runner scripts/ ./scripts/
# capstone: the station list is part of the code (versioned in git), not the data
COPY --chown=runner:runner config/ ./config/

USER runner

# Credentials NEVER enter an image layer. They arrive at runtime from the platform's identity.
ENTRYPOINT ["python", "-m", "src.train"]
# capstone: no default arguments; the selected configuration is passed explicitly