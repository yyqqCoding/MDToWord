#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT=/opt/mdtoword
readonly DEPLOY_ROOT="${PROJECT_ROOT}/deploy/agent"
readonly INSTALLED_AGENTCTL=/usr/local/sbin/mdtoword-agentctl

if [[ "${EUID}" -ne 0 ]]; then
    echo "请使用 sudo 运行部署脚本。" >&2
    exit 1
fi

# 更新入口必须先停止领取；首次尚未安装管理命令时使用仓库中的同版本脚本。
if [[ -x "${INSTALLED_AGENTCTL}" ]]; then
    "${INSTALLED_AGENTCTL}" disable
elif [[ -f "${DEPLOY_ROOT}/mdtoword-agentctl" ]]; then
    bash "${DEPLOY_ROOT}/mdtoword-agentctl" disable
else
    echo "缺少 Agent 管理命令，无法安全停止 Scheduler。" >&2
    exit 1
fi

if [[ ! -f "${DEPLOY_ROOT}/install.sh" ]]; then
    echo "缺少部署文件：${DEPLOY_ROOT}/install.sh" >&2
    exit 1
fi

bash "${DEPLOY_ROOT}/install.sh"

# enable 会重新审计并要求维护者输入 ENABLE；取消或失败时保持 Scheduler 关闭。
"${INSTALLED_AGENTCTL}" enable
"${INSTALLED_AGENTCTL}" status
