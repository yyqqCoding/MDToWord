"""GitHub App 的 PR/Issue 最小权限预检；不执行任何仓库写操作。"""

import asyncio
import json

import httpx

from agent.config import AgentConfig
from agent.domain.errors import AgentError, ConfigurationError
from agent.publishing.github import GitHubAppTokenProvider


async def check_github_app(config: AgentConfig) -> None:
    """申请一个短期安装令牌，以验证 App 安装范围和最小权限。"""

    repository, *_ = config.require_stage_c_controller_settings()
    app_id, private_key, api_url, *_ = config.require_stage_f_publisher_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        pr_provider = GitHubAppTokenProvider(
            repository,
            app_id=app_id,
            private_key=private_key,
            client=client,
            api_url=api_url,
        )
        issue_provider = GitHubAppTokenProvider(
            repository,
            app_id=app_id,
            private_key=private_key,
            client=client,
            api_url=api_url,
            permissions={"issues": "write"},
        )
        # 令牌仅在内存中用于权限校验，不输出、不保存，也不执行 GitHub 写操作。
        await pr_provider.get_token()
        await issue_provider.get_token()


def main() -> int:
    try:
        asyncio.run(check_github_app(AgentConfig.from_env()))
    except (ConfigurationError, AgentError) as exc:
        print(json.dumps({"error": exc.error_code}, sort_keys=True))
        return 1
    except Exception as exc:  # pragma: no cover - 外部服务安全边界
        print(
            json.dumps(
                {"error": "unexpected_error", "type": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps({"status": "github_app_ready"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
