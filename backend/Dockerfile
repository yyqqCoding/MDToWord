FROM python:3.11-slim AS mermaid-runtime

ENV PUPPETEER_SKIP_DOWNLOAD=true
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/mermaid
COPY mermaid/package.json mermaid/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund


FROM python:3.11-slim

# 与 Sandbox 使用相同基础运行时层，确保渲染依赖一致并复用镜像缓存。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PUPPETEER_SKIP_DOWNLOAD=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        nodejs \
        pandoc \
    && rm -rf /var/lib/apt/lists/*

# 构建阶段安装依赖；最终镜像只保留 Node 运行时和固定 Mermaid 模块，不包含 npm。
COPY --from=mermaid-runtime /opt/mermaid/node_modules /opt/mdtoword/mermaid/node_modules
RUN ln -s /opt/mdtoword/mermaid/node_modules/.bin/mmdc /usr/local/bin/mmdc
COPY mermaid/puppeteer-config.json mermaid/mermaid-config.json /opt/mdtoword/

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
