FROM python:3.11-slim AS mermaid-runtime

ENV PUPPETEER_SKIP_DOWNLOAD=true
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/mermaid
COPY backend/mermaid/package.json backend/mermaid/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund


FROM python:3.11-slim

# 禁止写入字节码和自动加载宿主未知的 pytest 插件，减少运行时副作用。
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

# 与生产镜像使用同一锁文件；最终 Sandbox 不保留 npm，模型也不能运行时安装依赖。
COPY --from=mermaid-runtime /opt/mermaid/node_modules /opt/mdtoword/mermaid/node_modules
RUN ln -s /opt/mdtoword/mermaid/node_modules/.bin/mmdc /usr/local/bin/mmdc
COPY backend/mermaid/puppeteer-config.json backend/mermaid/mermaid-config.json /opt/mdtoword/

WORKDIR /opt/build
# 只复制执行后端测试所需的包，不把 Agent、Git 元数据或本地配置放入镜像。
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/app ./backend/app
# DOCX 断言来自只读镜像层，不在模型可编辑的 workspace 中。
COPY backend/tests/docx_assertions.py /opt/trusted/docx_assertions.py
RUN pip install --no-cache-dir "./backend[dev]" \
    && rm -rf /opt/build

# 与 Docker Runner 固定的 UID/GID 保持一致，任务容器始终以非 root 运行。
RUN useradd --uid 65532 --no-create-home --shell /usr/sbin/nologin sandbox
USER 65532:65532
WORKDIR /workspace/backend

ENTRYPOINT ["python"]
