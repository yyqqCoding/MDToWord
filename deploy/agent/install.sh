#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT=/opt/mdtoword
readonly DEPLOY_ROOT="${PROJECT_ROOT}/deploy/agent"

if [[ "${EUID}" -ne 0 ]]; then
    echo "请使用 sudo 运行安装脚本。" >&2
    exit 1
fi

for required in \
    /etc/mdtoword/controller.env \
    /etc/mdtoword/worker.env \
    "${PROJECT_ROOT}/.venv/bin/python" \
    "${DEPLOY_ROOT}/mdtoword-agentctl"; do
    if [[ ! -e "${required}" ]]; then
        echo "缺少部署前置文件：${required}" >&2
        exit 1
    fi
done

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

# 安装动作保持安全默认：Worker 常驻，Scheduler 明确关闭，审核后再单独 enable。
systemctl enable --now mdtoword-worker.service
/usr/local/sbin/mdtoword-agentctl disable
/usr/local/sbin/mdtoword-agentctl audit

echo
echo "安装完成；Scheduler 仍为关闭状态。"
echo "确认审计统计后执行：sudo mdtoword-agentctl enable"
