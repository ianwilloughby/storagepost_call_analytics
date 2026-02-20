FROM mcr.microsoft.com/devcontainers/base:ubuntu-22.04

# ── System packages ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    curl \
    unzip \
    zip \
    git \
    jq \
    make \
    wget \
    software-properties-common \
    && rm -rf /var/lib/apt/lists/*

# ── Terraform ──────────────────────────────────────────────────────────────────
ARG TERRAFORM_VERSION=1.7.5
RUN wget -q https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip \
    && unzip terraform_${TERRAFORM_VERSION}_linux_amd64.zip \
    && mv terraform /usr/local/bin/ \
    && rm terraform_${TERRAFORM_VERSION}_linux_amd64.zip

# Verify
RUN terraform --version

# ── Helper scripts in PATH ────────────────────────────────────────────────────
COPY scripts/ /usr/local/bin/project-scripts/
RUN chmod +x /usr/local/bin/project-scripts/*.sh

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /workspace
