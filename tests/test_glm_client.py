"""glm_client 单元测试: mock 降级路径 + GLM API 路径。

覆盖 GLMLayoutGenerator:
- 无 ZHIPU_API_KEY → 自动降级为 MockLayoutGenerator (use_mock=True)
- 有 key → 构造 prompt, 调用 ZhipuAI, 解析 JSON 返回 ChunkLayout

本模块是 generate_world --use-glm 的核心; mock 降级保证离线可跑,
GLM 路径用 mock client 验证 prompt 构造与 response 解析 (不触及真实 API)。
"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import MagicMock

import pytest

from pipeline.glm_client import GLMLayoutGenerator
from pipeline.mock_layout import MockLayoutGenerator
from pipeline.schema import ChunkLayout


def _run(coro):
    """同步运行 async 方法 (项目无 async 测试配置, 用 asyncio.run)。"""
    return asyncio.run(coro)


def _install_fake_zhipuai(monkeypatch) -> MagicMock:
    """注入 fake zhipuai 模块, 使 GLM 路径可实例化 (不依赖真实 SDK)。

    返回 fake_client (= ZhipuAI() 返回值), 测试在其上设置 return_value。
    """
    fake_module = MagicMock()
    fake_client = MagicMock()
    fake_module.ZhipuAI.return_value = fake_client
    monkeypatch.setitem(sys.modules, "zhipuai", fake_module)
    return fake_client


def _fake_response(layout: ChunkLayout) -> MagicMock:
    """构造 choices[0].message.content = layout JSON 的 fake response。"""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = layout.model_dump_json()
    return mock_response


# ============================================================
# Mock 降级路径 (无 ZHIPU_API_KEY)
# ============================================================


class TestMockFallback:
    """无 API key 时自动降级为 MockLayoutGenerator。"""

    def test_no_api_key_falls_back_to_mock(self, monkeypatch):
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        gen = GLMLayoutGenerator()
        assert gen.use_mock is True

    def test_empty_api_key_falls_back_to_mock(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "")
        gen = GLMLayoutGenerator()
        assert gen.use_mock is True

    def test_mock_path_returns_chunk_layout(self, monkeypatch):
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        gen = GLMLayoutGenerator()
        layout = _run(gen.generate_chunk(0, 0, world_seed=42))
        assert isinstance(layout, ChunkLayout)

    def test_mock_path_sets_world_seed(self, monkeypatch):
        """mock 路径将 world_seed 传递给内部 MockLayoutGenerator。"""
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        gen = GLMLayoutGenerator()
        layout = _run(gen.generate_chunk(0, 0, world_seed=123))
        assert layout.world_seed == 123

    def test_mock_path_deterministic(self, monkeypatch):
        """相同 seed+chunk → 相同 layout (mock 路径确定性)。"""
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        gen1 = GLMLayoutGenerator()
        gen2 = GLMLayoutGenerator()
        l1 = _run(gen1.generate_chunk(3, 5, world_seed=42))
        l2 = _run(gen2.generate_chunk(3, 5, world_seed=42))
        assert l1.model_dump_json() == l2.model_dump_json()

    def test_mock_path_matches_direct_mock_generator(self, monkeypatch):
        """GLMLayoutGenerator mock 路径与直接 MockLayoutGenerator 字节一致。"""
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        gen = GLMLayoutGenerator()
        direct = MockLayoutGenerator(world_seed=42)
        l1 = _run(gen.generate_chunk(0, 0, world_seed=42))
        l2 = direct.generate_chunk(0, 0)
        assert l1.model_dump_json() == l2.model_dump_json()


# ============================================================
# GLM API 路径 (mock ZhipuAI client)
# ============================================================


class TestGLMApiPath:
    """有 API key 时走 GLM 路径, mock ZhipuAI client (不触及真实 API)。"""

    def test_with_api_key_uses_glm(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "fake-key-xxx")
        _install_fake_zhipuai(monkeypatch)
        gen = GLMLayoutGenerator()
        assert gen.use_mock is False

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        """显式 api_key 优先于 env ZHIPU_API_KEY。"""
        monkeypatch.setenv("ZHIPU_API_KEY", "env-key")
        _install_fake_zhipuai(monkeypatch)
        gen = GLMLayoutGenerator(api_key="explicit-key")
        assert gen.use_mock is False
        fake_module = sys.modules["zhipuai"]
        fake_module.ZhipuAI.assert_called_once_with(api_key="explicit-key")

    def test_none_api_key_uses_env_value(self, monkeypatch):
        """api_key=None 时 or 短路取 env ZHIPU_API_KEY (走 GLM 路径)。"""
        monkeypatch.setenv("ZHIPU_API_KEY", "env-key")
        _install_fake_zhipuai(monkeypatch)
        gen = GLMLayoutGenerator(api_key=None)
        assert gen.use_mock is False
        fake_module = sys.modules["zhipuai"]
        fake_module.ZhipuAI.assert_called_once_with(api_key="env-key")

    def test_glm_path_parses_response(self, monkeypatch):
        """GLM 路径: mock client 返回 JSON, 解析为 ChunkLayout。"""
        fake_client = _install_fake_zhipuai(monkeypatch)
        sample = MockLayoutGenerator(world_seed=42).generate_chunk(0, 0)
        fake_client.chat.completions.create.return_value = _fake_response(sample)

        gen = GLMLayoutGenerator(api_key="fake")
        layout = _run(gen.generate_chunk(0, 0, world_seed=42))
        assert isinstance(layout, ChunkLayout)
        assert gen.use_mock is False
        fake_client.chat.completions.create.assert_called_once()

    def test_glm_path_passes_config(self, monkeypatch):
        """GLM 路径: model/temperature/max_tokens/response_format 传入 create。"""
        fake_client = _install_fake_zhipuai(monkeypatch)
        sample = MockLayoutGenerator(world_seed=42).generate_chunk(0, 0)
        fake_client.chat.completions.create.return_value = _fake_response(sample)

        gen = GLMLayoutGenerator(api_key="fake", model="glm-4.6", temperature=0.5)
        _run(gen.generate_chunk(0, 0, world_seed=42))
        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "glm-4.6"
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["max_tokens"] == 4000
        assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_glm_path_constructs_prompt(self, monkeypatch):
        """system/user prompt 含村庄规划指令与 chunk 坐标 + seed。"""
        fake_client = _install_fake_zhipuai(monkeypatch)
        sample = MockLayoutGenerator(world_seed=42).generate_chunk(0, 0)
        fake_client.chat.completions.create.return_value = _fake_response(sample)

        gen = GLMLayoutGenerator(api_key="fake")
        _run(gen.generate_chunk(7, 3, world_seed=42))
        messages = fake_client.chat.completions.create.call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "村庄规划师" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        # USER_TEMPLATE 含 chunk 坐标与 seed
        assert "chunk (7, 3)" in messages[1]["content"]
        assert "世界种子: 42" in messages[1]["content"]

    def test_glm_path_invalid_json_raises(self, monkeypatch):
        """GLM 返回非 JSON → json.loads fail-fast (不静默降级)。"""
        fake_client = _install_fake_zhipuai(monkeypatch)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not valid json {{"
        fake_client.chat.completions.create.return_value = mock_response

        gen = GLMLayoutGenerator(api_key="fake")
        with pytest.raises(json.JSONDecodeError):
            _run(gen.generate_chunk(0, 0, world_seed=42))
