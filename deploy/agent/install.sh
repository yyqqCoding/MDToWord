#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT=/opt/mdtoword
readonly DEPLOY_ROOT="${PROJECT_ROOT}/deploy/agent"
readonly PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
readonly REQUIREMENTS_LOCK="${DEPLOY_ROOT}/requirements.lock"

if [[ "${EUID}" -ne 0 ]]; then
    echo "请使用 sudo 运行安装脚本。" >&2
    exit 1
fi

for required in \
    /etc/mdtoword/controller.env \
    /etc/mdtoword/worker.env \
    "${PYTHON_BIN}" \
    "${REQUIREMENTS_LOCK}" \
    "${DEPLOY_ROOT}/mdtoword-agentctl"; do
    if [[ ! -e "${required}" ]]; then
        echo "缺少部署前置文件：${required}" >&2
        exit 1
    fi
done

# 直接调用 install.sh 也必须先停掉两个进程；不能在它们正在从虚拟环境导入模块时原地
# 更新依赖。任何安装失败都会保持 Scheduler 与 Worker 关闭，由维护者修正后重新部署。
systemctl disable --now mdtoword-scheduler.service 2>/dev/null || true
systemctl stop mdtoword-worker.service 2>/dev/null || true

# uv.lock 是解析权威，requirements.lock 是供无 uv 的生产主机使用的精确导出。uv 创建的
# 虚拟环境默认可能没有 pip，因此先使用 Python 自带 ensurepip 离线补齐安装器。
if ! "${PYTHON_BIN}" -m pip --version >/dev/null 2>&1; then
    "${PYTHON_BIN}" -m ensurepip --upgrade
fi
"${PYTHON_BIN}" -m pip install \
    --disable-pip-version-check \
    --no-input \
    --no-deps \
    --require-hashes \
    --requirement "${REQUIREMENTS_LOCK}"
"${PYTHON_BIN}" -m pip check
"${PYTHON_BIN}" -c 'import langchain, langchain_openai; print("agent_dependencies_ready")'

install -o root -g root -m 0750 \
    "${DEPLOY_ROOT}/mdtoword-agentctl" \
    /usr/local/sbin/mdtoword-agentctl
install -o root -g root -m 0644 \
    "${DEPLOY_ROOT}/systemd/mdtoword-worker.service" \
    /etc/systemd/system/mdtoword-worker.service
install -o root -g root -m 0644 \
    "${DEPLOY_ROOT}/systemd/mdtoword-scheduler.service" \
    /etc/systemd/system/mdtoword-scheduler.service

systemd-analyze verify \
    /etc/systemd/system/mdtoword-worker.service \
    /etc/systemd/system/mdtoword-scheduler.service
systemctl daemon-reload

# 安装动作保持安全默认：Worker 加载本次更新，Scheduler 明确关闭，审核后再单独 enable。
systemctl enable mdtoword-worker.service
systemctl restart mdtoword-worker.service
/usr/local/sbin/mdtoword-agentctl disable
/usr/local/sbin/mdtoword-agentctl audit

echo
echo "安装完成；Scheduler 仍为关闭状态。"
echo "确认审计统计后执行：sudo mdtoword-agentctl enable"
