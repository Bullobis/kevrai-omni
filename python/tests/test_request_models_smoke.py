"""冒烟测试：sidecar 里每一个请求体模型都必须能真正构造出来。

为什么需要这条测试：**pydantic v2 的保留名错误只在类定义（import）时炸**，
而且炸的方式是让 11 个测试文件在**收集阶段**全部失败——看起来像"环境坏了"，
实际上是某处把一个字段命名为 ``model_config``。

单纯"文件能 import"还不够，所以这里真的去构造每一个模型类。
只要有人在 main.py 里新增/改名一个请求体字段，这条测试会立刻抓住。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, ValidationError  # noqa: E402

from app import main as app_main  # noqa: E402

#: 历史遗留的 ``model_`` 前缀字段（改动前就存在）。
#: 列在这里是为了**冻结**它们——不允许新增，但也不因它们的存在让测试失败。
#: 它们只触发 pydantic 的 protected-namespace 警告，功能上无害。
LEGACY_MODEL_PREFIX_FIELDS = frozenset({
    "SettingsUpdate.model_dir",
    "MnnLoadReq.model_dir",
    "MnnLoadReq.model_name",
    "DramaRenderPlanReq.model_choices",
    "LtxGenerateReq.model_id",
    "ThreeDGenerateReq.model_id",
    "RolePinReq.model_id",
})


def _collect_request_models() -> list[type[BaseModel]]:
    """main.py 里自己定义的所有 pydantic 模型（不含第三方导入的）。"""
    models: list[type[BaseModel]] = []
    for obj in vars(app_main).values():
        if not inspect.isclass(obj) or not issubclass(obj, BaseModel):
            continue
        if obj is BaseModel:
            continue
        if getattr(obj, "__module__", "") != app_main.__name__:
            continue
        models.append(obj)
    return models


def test_there_are_request_models_to_check() -> None:
    """守卫测试自身：一条都没收到说明收集逻辑坏了，必须报错。"""
    assert len(_collect_request_models()) > 0


def test_every_request_model_is_constructible() -> None:
    """每个模型类都必须能实例化。

    注意区分两种失败：
      * "少必填字段" —— 无参构造时**预期**会报 missing，不代表类有问题；
      * 其它任何异常 —— 类本身构造不了，必须失败。
    """
    failures: list[str] = []
    for model in _collect_request_models():
        try:
            model()
        except ValidationError as e:
            # 只有 missing 类错误才算"预期"，其它校验错误仍然是可疑的。
            types = {err.get("type") for err in e.errors()}
            if types <= {"missing"}:
                continue
            failures.append(f"{model.__name__}: unexpected ValidationError {types}")
        except Exception as e:  # noqa: BLE001 - 目的就是收集所有炸掉的类
            failures.append(f"{model.__name__}: {type(e).__name__}: {e}")
    assert not failures, "以下请求体模型无法构造：\n" + "\n".join(failures)


def test_no_field_uses_pydantic_reserved_names() -> None:
    """字段名不得撞 pydantic v2 的保留名（``model_config`` 会让类直接无法定义）。"""
    reserved = {"model_config"}
    bad: list[str] = []
    for model in _collect_request_models():
        for fname in getattr(model, "model_fields", {}) or {}:
            if fname in reserved:
                bad.append(f"{model.__name__}.{fname}")
    assert not bad, "字段名撞上 pydantic 保留名：" + ", ".join(bad)


def test_no_new_model_prefixed_fields() -> None:
    """新代码不得再引入 ``model_`` 前缀字段（protected namespace 会刷警告）。"""
    offenders: list[str] = []
    for model in _collect_request_models():
        for fname in getattr(model, "model_fields", {}) or {}:
            if fname.startswith("model_") and f"{model.__name__}.{fname}" not in LEGACY_MODEL_PREFIX_FIELDS:
                offenders.append(f"{model.__name__}.{fname}")
    assert not offenders, "新增了 model_ 前缀字段：" + ", ".join(offenders)


def test_params_resolve_roundtrip() -> None:
    """新增的参数解析端点：字段能进能出。"""
    req = app_main.ParamsResolveReq(scenario="coding", recommended={"temperature": 0.5})
    assert req.recommended == {"temperature": 0.5}
    assert req.scenario == "coding"


def test_params_resolve_rejects_unknown_scenario() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        app_main.params_resolve(
            app_main.ParamsResolveReq(scenario="不存在这个场景")
        )
    assert ei.value.status_code == 400


def test_params_resolve_reports_sources_and_notes() -> None:
    """端点要能说清"每项从哪来"以及"哪些值被改了"。"""
    out = app_main.params_resolve(
        app_main.ParamsResolveReq(
            scenario="coding",
            recommended={"temperature": 999.0, "scheduler": "dpm_karras"},
        )
    )
    assert out["sources"]["temperature"] == "model"
    assert out["sources"]["max_tokens"] == "scenario"
    assert out["notes"], "被修正的值必须有人话提示"
    assert any("压到" in n for n in out["notes"])
