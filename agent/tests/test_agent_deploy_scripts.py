import re
import subprocess
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENTCTL = PROJECT_ROOT / "deploy/agent/mdtoword-agentctl"
INSTALL = PROJECT_ROOT / "deploy/agent/install.sh"
PRODUCTION_LOCK = PROJECT_ROOT / "deploy/agent/requirements.lock"


def _run_worker_check(http_code: str) -> subprocess.CompletedProcess[str]:
    command = f'''
source "{AGENTCTL}"
systemctl() {{ return 0; }}
curl() {{ printf '%s' "{http_code}"; }}
sleep() {{ :; }}
runuser() {{ return 0; }}
ss() {{ printf '%s\n' 'LISTEN 0 128 127.0.0.1:8090 0.0.0.0:*'; }}
journalctl() {{ return 0; }}
check_worker
'''
    return subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )


def test_worker_readiness_accepts_unauthorized_probe_after_authentication_starts():
    result = _run_worker_check("401")

    assert result.returncode == 0
    assert "worker_ready" in result.stdout


def test_worker_readiness_rejects_pre_authentication_bad_request_behavior():
    result = _run_worker_check("400")

    assert result.returncode == 1
    assert "最后一次 HTTP 状态：400" in result.stderr


def test_install_stops_services_before_syncing_locked_dependencies():
    script = INSTALL.read_text("utf-8")

    stop_scheduler = script.index("systemctl disable --now mdtoword-scheduler.service")
    stop_worker = script.index("systemctl stop mdtoword-worker.service")
    ensure_pip = script.index('-m ensurepip --upgrade')
    install_dependencies = script.index('--requirement "${REQUIREMENTS_LOCK}"')
    check_dependencies = script.index('-m pip check')
    restart_worker = script.index("systemctl restart mdtoword-worker.service")

    assert max(stop_scheduler, stop_worker) < ensure_pip
    assert ensure_pip < install_dependencies < check_dependencies < restart_worker
    assert "--no-deps" in script
    assert "--require-hashes" in script


def test_production_lock_pins_every_direct_runtime_dependency():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text("utf-8"))
    locked = PRODUCTION_LOCK.read_text("utf-8").lower().replace("_", "-")

    for dependency in project["project"]["dependencies"]:
        match = re.match(r"[a-z0-9_.-]+", dependency, re.IGNORECASE)
        assert match is not None
        name = match.group(0).lower().replace("_", "-")
        assert re.search(rf"^{re.escape(name)}==", locked, re.MULTILINE), name
