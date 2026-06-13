FROM node:20

ARG TZ
ENV TZ="$TZ"

ARG CLAUDE_CODE_VERSION=latest

# Install basic development tools and iptables/ipset
RUN apt-get update && apt-get install -y --no-install-recommends \
  less \
  git \
  procps \
  sudo \
  fzf \
  zsh \
  man-db \
  unzip \
  gnupg2 \
  gh \
  iptables \
  ipset \
  iproute2 \
  dnsutils \
  aggregate \
  jq \
  nano \
  vim \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# Ensure default node user has access to /usr/local/share
RUN mkdir -p /usr/local/share/npm-global && \
  chown -R node:node /usr/local/share

ARG USERNAME=node

# Persist bash history.
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
  && mkdir /commandhistory \
  && touch /commandhistory/.bash_history \
  && chown -R $USERNAME /commandhistory

# Set `DEVCONTAINER` environment variable to help with orientation
ENV DEVCONTAINER=true

# Create workspace and config directories and set permissions
RUN mkdir -p /workspace /home/node/.claude && \
  chown -R node:node /workspace /home/node/.claude

WORKDIR /workspace

ARG GIT_DELTA_VERSION=0.18.2
RUN ARCH=$(dpkg --print-architecture) && \
  wget "https://github.com/dandavison/delta/releases/download/${GIT_DELTA_VERSION}/git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb" && \
  sudo dpkg -i "git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb" && \
  rm "git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb"

# Set up non-root user
USER node

# Install global packages
ENV NPM_CONFIG_PREFIX=/usr/local/share/npm-global
ENV PATH=$PATH:/usr/local/share/npm-global/bin

# Set the default shell to zsh rather than sh
ENV SHELL=/bin/zsh

# Set the default editor and visual
ENV EDITOR=nano
ENV VISUAL=nano

# Default powerline10k theme
ARG ZSH_IN_DOCKER_VERSION=1.2.0
RUN sh -c "$(wget -O- https://github.com/deluan/zsh-in-docker/releases/download/v${ZSH_IN_DOCKER_VERSION}/zsh-in-docker.sh)" -- \
  -p git \
  -p fzf \
  -a "source /usr/share/doc/fzf/examples/key-bindings.zsh" \
  -a "source /usr/share/doc/fzf/examples/completion.zsh" \
  -a "export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
  -x

# Install Claude
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}


# Copy and set up firewall script
COPY init-firewall.sh /usr/local/bin/
USER root
RUN chmod +x /usr/local/bin/init-firewall.sh && \
  echo "node ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh" > /etc/sudoers.d/node-firewall && \
  chmod 0440 /etc/sudoers.d/node-firewall
USER node

# --- AI-Lab addition: uv for Python projects (project-level venvs only, per 001-policy) ---
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/home/node/.local/bin:${PATH}"

# --- AI-Lab addition: Railway CLI (auth via RAILWAY_API_TOKEN env, no login per project) ---
RUN npm install -g @railway/cli

# --- AI-Lab addition: tmux for long-running loops (survive closing VS Code) ---
USER root
RUN apt-get update && apt-get install -y --no-install-recommends tmux \
  && apt-get clean && rm -rf /var/lib/apt/lists/*
USER node

# --- AI-Lab addition: PostgreSQL server for pytest-postgresql (project-specific) ---
# AI-Agent-for-Trading needs the Postgres SERVER binaries (initdb/pg_ctl/postgres),
# not just the client, because pytest-postgresql (PRD §9.3) spins up an ephemeral
# cluster for integration/e2e tests. The Debian 'postgresql' package installs these
# under /usr/lib/postgresql/<ver>/bin, which is NOT on PATH by default — so we add it.
# Scope: this repo only (not promoted to the canonical scaffold) — not every vibe
# project uses a database.
USER root
RUN apt-get update && apt-get install -y --no-install-recommends postgresql \
  && apt-get clean && rm -rf /var/lib/apt/lists/*
# Expose the Postgres server binaries on PATH (resolve the versioned bin dir dynamically).
RUN PG_BIN=$(dirname "$(ls /usr/lib/postgresql/*/bin/pg_ctl | head -1)") \
  && echo "export PATH=\"$PG_BIN:\$PATH\"" >> /etc/profile.d/postgres-path.sh \
  && chmod 0644 /etc/profile.d/postgres-path.sh
ENV PATH="/usr/lib/postgresql/15/bin:${PATH}"
USER node
