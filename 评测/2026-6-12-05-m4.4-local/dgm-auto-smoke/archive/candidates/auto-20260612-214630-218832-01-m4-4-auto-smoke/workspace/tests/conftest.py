"""pytest 配置：asyncio_mode=auto 已在 pyproject 设置，故 async 测试无需手动标注。"""
import pytest

from mu.tools import ToolRegistry


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()
