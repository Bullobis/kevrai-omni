"""Neuron-Env (K-Cortex 第三切片) 回归测试 — app.env pip 引导路径.

本文件只覆盖第三切片审计中确认修复的两处真实问题：
  1. upgrade_pip_package 必须真正传入 ``--upgrade``（否则 pip 对已安装包
     报 "Requirement already satisfied" 并退出 0，升级按钮静默空操作）。
  2. pip 安装/升级失败必须抛出中文、可定位的 InstallError（与 sidecar
     其余 API 层中文提示一致），并附带错误输出尾部。

所有用例通过 patch app.env._run 拦截子进程，绝不真实联网或 pip install。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.env import InstallError, install_pip_package, upgrade_pip_package


def test_neuron3_upgrade_uses_upgrade_flag_and_no_pin():
    with patch("app.env._run", return_value=(0, "ok", "")) as m:
        upgrade_pip_package("fastapi")
    cmd = m.call_args.args[0]
    assert m.call_args.kwargs.get("timeout") == 300.0
    assert "--upgrade" in cmd, "升级必须传入 --upgrade，否则 pip 静默 no-op"
    # 升级不应钉死版本
    assert "==" not in cmd[-1], cmd
    assert cmd[-1] == "fastapi"


def test_neuron3_install_default_has_no_upgrade_flag():
    with patch("app.env._run", return_value=(0, "ok", "")) as m:
        install_pip_package("fastapi")
    cmd = m.call_args.args[0]
    assert "--upgrade" not in cmd, "普通安装不应带 --upgrade"


def test_neuron3_install_pins_version_when_given():
    with patch("app.env._run", return_value=(0, "ok", "")) as m:
        install_pip_package("fastapi", version="0.111.0")
    cmd = m.call_args.args[0]
    assert cmd[-1] == "fastapi==0.111.0"


def test_neuron3_default_cn_mirrors_appended():
    """审计点1：默认应挂载多个国内镜像（非单一硬编码源）。"""
    with patch("app.env._run", return_value=(0, "ok", "")) as m:
        install_pip_package("fastapi")
    cmd = m.call_args.args[0]
    for mirror in (
        "https://mirrors.aliyun.com/pypi/simple/",
        "https://pypi.tuna.tsinghua.edu.cn/simple/",
        "https://mirrors.huaweicloud.com/repository/pypi/simple/",
    ):
        assert mirror in cmd
    # 镜像以 --extra-index-url 追加
    assert cmd.count("--extra-index-url") == 3


def test_neuron3_install_failure_chinese_message_with_tail():
    """审计点3/5：失败必须是中文可定位提示，并保留错误尾部。"""
    with patch("app.env._run", return_value=(1, "", "Could not find a version")), \
            pytest.raises(InstallError) as exc:
        install_pip_package("fastapi")
    msg = str(exc.value)
    assert "安装" in msg
    assert "fastapi" in msg
    assert "Could not find a version" in msg, "应保留 pip 错误尾部便于定位"
    assert "pip install failed" not in msg, "旧英文提示应被中文化"


def test_neuron3_upgrade_failure_chinese_message():
    with patch("app.env._run", return_value=(1, "", "network is unreachable")), \
            pytest.raises(InstallError) as exc:
        upgrade_pip_package("uvicorn")
    msg = str(exc.value)
    assert "升级" in msg
    assert "uvicorn" in msg
    assert "network is unreachable" in msg


def test_neuron3_success_returns_dict_shape():
    with patch("app.env._run", return_value=(0, "installed tail output", "")):
        out = upgrade_pip_package("pydantic")
    assert out["ok"] is True
    assert out["name"] == "pydantic"
    assert out["output_tail"] == "installed tail output"


def test_neuron3_timeout_is_passed_through():
    """审计点3：pip 引导子进程必须带超时（300s）。"""
    with patch("app.env._run", return_value=(0, "", "")) as m:
        install_pip_package("x")
    assert m.call_args.kwargs.get("timeout") == 300.0
