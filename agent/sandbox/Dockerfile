FROM python:3.11-slim

# 禁止写入字节码和自动加载宿主未知的 pytest 插件，减少运行时副作用。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends pandoc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/build
# 只复制执行后端测试所需的包，不把 Agent、Git 元数据或本地配置放入镜像。
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/app ./backend/app
RUN pip install --no-cache-dir "./backend[dev]" \
    && rm -rf /opt/build

# 与 Docker Runner 固定的 UID/GID 保持一致，任务容器始终以非 root 运行。
RUN useradd --uid 65532 --no-create-home --shell /usr/sbin/nologin sandbox
USER 65532:65532
WORKDIR /workspace/backend

ENTRYPOINT ["python"]
