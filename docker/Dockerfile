FROM node:20-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    jq \
    python3 \
    python3-pip \
    python3-venv \
    tini \
    zsh \
 && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
 && mv /root/.local/bin/uv /usr/local/bin/uv

RUN python3 -m pip install --no-cache-dir --break-system-packages \
    build \
    pipx

RUN npm install -g @openai/codex

RUN useradd --create-home --shell /bin/bash tester \
 && mkdir -p /home/tester/.local/bin /home/tester/.local/lib /home/tester/.npm \
 && chown -R tester:tester /home/tester

WORKDIR /workspace
COPY . /workspace
RUN chown -R tester:tester /workspace

USER tester
ENV HOME=/home/tester
ENV NPM_CONFIG_PREFIX=/home/tester/.local
ENV PATH=/home/tester/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash"]
