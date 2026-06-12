"""沙箱 provider 抽象：make_environment 选择；DockerEnvironment（docker-gated）。"""
from __future__ import annotations

import shutil

import pytest

from mu.environment import Environment, LocalEnvironment, make_environment


def test_make_environment_selection():
    local = make_environment("local")
    assert isinstance(local, LocalEnvironment)
    assert isinstance(local, Environment)  # 满足 Protocol
    with pytest.raises(ValueError):
        make_environment("nope")


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")
async def test_docker_env_runs_bash(tmp_path):
    env = make_environment("docker", workspace=str(tmp_path))
    res = await env.run_bash("echo hello-docker")
    assert "hello-docker" in res.stdout
    assert res.exit_code == 0
