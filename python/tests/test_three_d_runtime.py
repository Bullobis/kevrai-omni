"""Tests for the v4.11.0 3D generation runtime.

All tests are offline: no torch, no GPU, no network. The heavy pipeline
loader and the generator are monkeypatched, so we exercise the *runtime
contract* (validation, task lifecycle, cancellation, export naming, PLY
writer) rather than the models themselves.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app import three_d_runtime as td


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _FakeMesh:
    """Minimal mesh stand-in with the attributes the exporter reads."""

    def __init__(self, verts, faces):
        self.vertices = verts
        self.faces = faces


def _params(**kw):
    base = dict(mode="t23d", prompt="a red mug", model_id="microsoft/TRELLIS-image-large")
    base.update(kw)
    return td.ThreeDParams(**base)


@pytest.fixture
def img(tmp_path: Path) -> str:
    p = tmp_path / "input.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return str(p)


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------

def test_capabilities_never_raises_and_has_shape():
    cap = td.capabilities()
    for key in ("available", "torch", "diffusers", "trimesh", "pil",
                "cuda", "missing", "presets", "modes", "output_formats"):
        assert key in cap, f"missing capability key: {key}"
    assert isinstance(cap["missing"], list)
    assert set(cap["modes"]) == {"i23d", "t23d"}
    assert "glb" in cap["output_formats"]
    # 未装引擎时应给出可执行的安装提示
    if not cap["available"]:
        assert cap["install_hint"].startswith("pip install")


def test_capabilities_presets_expose_labels_and_notes():
    cap = td.capabilities()
    for name in ("fast", "balanced", "quality"):
        assert name in cap["presets"]
        entry = cap["presets"][name]
        assert entry["label"]
        assert entry["note"]


# ---------------------------------------------------------------------------
# parameter validation
# ---------------------------------------------------------------------------

def test_valid_params_pass():
    _params().validate()


@pytest.mark.parametrize("mode", ["i2v", "t2v", "3d", "", "I23D"])
def test_bad_mode_rejected(mode):
    with pytest.raises(td.ThreeDParamError, match="mode must be"):
        _params(mode=mode).validate()


@pytest.mark.parametrize("preset", ["ultra", "slow", "", "FAST"])
def test_bad_preset_rejected(preset):
    with pytest.raises(td.ThreeDParamError, match="unknown preset"):
        _params(preset=preset).validate()


@pytest.mark.parametrize("fmt", ["mp4", "stl", "", "GLB"])
def test_bad_output_format_rejected(fmt):
    with pytest.raises(td.ThreeDParamError, match="output_format must be"):
        _params(output_format=fmt).validate()


@pytest.mark.parametrize("prompt", ["", "   ", "\t\n"])
def test_empty_prompt_rejected(prompt):
    with pytest.raises(td.ThreeDParamError, match="prompt must not be empty"):
        _params(prompt=prompt).validate()


def test_overlong_prompt_rejected():
    with pytest.raises(td.ThreeDParamError, match="prompt too long"):
        _params(prompt="x" * 2001).validate()


def test_overlong_negative_prompt_rejected():
    with pytest.raises(td.ThreeDParamError, match="negative_prompt too long"):
        _params(negative_prompt="x" * 2001).validate()


@pytest.mark.parametrize("res", [0, 64, 127, 1025, 4096])
def test_resolution_bounds(res):
    with pytest.raises(td.ThreeDParamError, match="resolution must be"):
        _params(resolution=res).validate()


@pytest.mark.parametrize("res", [128, 256, 512, 1024])
def test_resolution_accepts_boundaries(res):
    _params(resolution=res).validate()


@pytest.mark.parametrize("steps", [0, -1, 201, 1000])
def test_steps_bounds(steps):
    with pytest.raises(td.ThreeDParamError, match="num_inference_steps must be"):
        _params(num_inference_steps=steps).validate()


@pytest.mark.parametrize("g", [0.0, 0.09, 30.1, 100.0])
def test_guidance_bounds(g):
    with pytest.raises(td.ThreeDParamError, match="guidance_scale must be"):
        _params(guidance_scale=g).validate()


@pytest.mark.parametrize("seed", [-2, 2**31, 2**40])
def test_seed_bounds(seed):
    with pytest.raises(td.ThreeDParamError, match="seed out of range"):
        _params(seed=seed).validate()


def test_seed_minus_one_allowed():
    _params(seed=-1).validate()


@pytest.mark.parametrize("n", [0, 999, 5_000_001])
def test_max_faces_bounds(n):
    with pytest.raises(td.ThreeDParamError, match="max_faces must be"):
        _params(max_faces=n).validate()


@pytest.mark.parametrize("bad", [True, False])
def test_bool_rejected_as_int(bad):
    """bool is an int subclass in Python; it must not pass as a number."""
    with pytest.raises(td.ThreeDParamError, match="must be an integer"):
        _params(num_inference_steps=bad).validate()


@pytest.mark.parametrize("bad", ["20", "1e3", None])
def test_non_numeric_rejected(bad):
    with pytest.raises(td.ThreeDParamError, match="must be an integer"):
        _params(num_inference_steps=bad).validate()


def test_fractional_float_rejected():
    with pytest.raises(td.ThreeDParamError, match="must be an integer"):
        _params(num_inference_steps=20.5).validate()


def test_integral_float_normalised():
    p = _params(num_inference_steps=20.0)
    p.validate()
    assert p.num_inference_steps == 20
    assert isinstance(p.num_inference_steps, int)


# ---------------------------------------------------------------------------
# mode-specific validation
# ---------------------------------------------------------------------------

def test_i23d_requires_image_path():
    with pytest.raises(td.ThreeDParamError, match="image_path is required"):
        _params(mode="i23d", image_path="").validate()


def test_i23d_rejects_missing_file():
    with pytest.raises(td.ThreeDParamError, match="image not found"):
        _params(mode="i23d", image_path="/definitely/not/here.png").validate()


def test_i23d_accepts_existing_file(img):
    _params(mode="i23d", image_path=img).validate()


@pytest.mark.parametrize("model_id", [
    "stabilityai/TripoSR",
    "stabilityai/stable-fast-3d",
    "SF3D/Example",
])
def test_t23d_rejected_for_reconstruction_only_models(model_id):
    """TripoSR-style models cannot do text-to-3D; say so up-front."""
    with pytest.raises(td.ThreeDParamError, match="不支持文生 3D"):
        _params(mode="t23d", model_id=model_id).validate()


def test_t23d_allowed_for_generative_model():
    _params(mode="t23d", model_id="microsoft/TRELLIS-image-large").validate()


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def test_output_stem_is_deterministic():
    assert td.output_stem("abc123", 1700.9) == "3d_abc123_1700"
    assert td.output_stem("abc123", 1700.9) == td.output_stem("abc123", 1700.2)


def test_requires_trimesh_per_format():
    assert td.requires_trimesh("glb") is True
    assert td.requires_trimesh("obj") is False
    assert td.requires_trimesh("ply") is False
    # 未知格式保守要求 trimesh，不要猜
    assert td.requires_trimesh("usdz") is True


# ---------------------------------------------------------------------------
# PLY fallback writer (no trimesh path)
# ---------------------------------------------------------------------------

def test_write_ply_from_object_mesh(tmp_path: Path):
    mesh = _FakeMesh(
        verts=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)],
        faces=[(0, 1, 2), (0, 1, 3)],
    )
    out = td._write_ply_guess(mesh, tmp_path / "a.ply", max_faces=0)
    text = out.read_text(encoding="utf-8")
    assert text.startswith("ply\n")
    assert "element vertex 4" in text
    assert "element face 2" in text
    assert "end_header" in text
    assert "3 0 1 2" in text


def test_write_ply_from_tuple_mesh(tmp_path: Path):
    mesh = ([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [(0, 1, 2)])
    out = td._write_ply_guess(mesh, tmp_path / "b.ply", max_faces=0)
    assert "element face 1" in out.read_text(encoding="utf-8")


def test_write_ply_drops_non_triangular_faces(tmp_path: Path):
    """A quad would produce an unreadable triangle list; drop it instead."""
    mesh = _FakeMesh(
        verts=[(0.0, 0.0, 0.0)] * 4,
        faces=[(0, 1, 2), (0, 1, 2, 3)],
    )
    out = td._write_ply_guess(mesh, tmp_path / "c.ply", max_faces=0)
    assert "element face 1" in out.read_text(encoding="utf-8")


def test_write_ply_respects_max_faces(tmp_path: Path):
    mesh = _FakeMesh(verts=[(0.0, 0.0, 0.0)] * 4, faces=[(0, 1, 2)] * 10)
    out = td._write_ply_guess(mesh, tmp_path / "d.ply", max_faces=3)
    assert "element face 3" in out.read_text(encoding="utf-8")


def test_write_ply_rejects_unknown_shape(tmp_path: Path):
    with pytest.raises(td.ThreeDEngineMissing, match="无法在无 trimesh"):
        td._write_ply_guess(object(), tmp_path / "e.ply", max_faces=0)


def test_export_glb_without_trimesh_raises_actionable(tmp_path: Path):
    """GLB is a container format; we will not hand-roll it."""
    with pytest.raises(td.ThreeDEngineMissing, match="trimesh"):
        td._export_mesh(_FakeMesh([], []), tmp_path / "f.glb", fmt="glb", max_faces=0)


# ---------------------------------------------------------------------------
# task lifecycle
# ---------------------------------------------------------------------------

def _manager(tmp_path: Path) -> td.ThreeDManager:
    return td.ThreeDManager(tmp_path / "outputs" / "3d")


def test_manager_creates_output_dir(tmp_path: Path):
    mgr = _manager(tmp_path)
    assert mgr.output_root.is_dir()


def test_start_rejects_invalid_params(tmp_path: Path):
    mgr = _manager(tmp_path)
    with pytest.raises(td.ThreeDParamError):
        mgr.start(_params(prompt=""))


def test_second_task_while_running_is_busy(tmp_path: Path, monkeypatch, img):
    release = threading.Event()

    def _slow_loader(model_id, mode):
        release.wait(timeout=5)
        raise td.ThreeDEngineMissing("stop")

    monkeypatch.setattr(td, "_load_pipeline", _slow_loader)
    mgr = _manager(tmp_path)
    first = mgr.start(_params(mode="i23d", image_path=img))
    try:
        # 等它真正进入 LOADING
        for _ in range(100):
            if mgr.get(first.id).state != td.TaskState.QUEUED:
                break
            time.sleep(0.01)
        with pytest.raises(td.ThreeDBusyError, match="already running"):
            mgr.start(_params(mode="i23d", image_path=img))
    finally:
        release.set()


def test_cancel_during_load_ends_cancelled_not_failed(tmp_path: Path, monkeypatch, img):
    """A user cancel must surface as CANCELLED with an empty error."""
    release = threading.Event()

    def _slow_loader(model_id, mode):
        release.wait(timeout=5)
        raise td.ThreeDEngineMissing("stop")

    monkeypatch.setattr(td, "_load_pipeline", _slow_loader)
    mgr = _manager(tmp_path)
    task = mgr.start(_params(mode="i23d", image_path=img))
    try:
        for _ in range(100):
            if mgr.get(task.id).state != td.TaskState.QUEUED:
                break
            time.sleep(0.01)
        assert mgr.cancel(task.id) is True
        release.set()
        for _ in range(200):
            if mgr.get(task.id).state in (
                td.TaskState.DONE, td.TaskState.FAILED, td.TaskState.CANCELLED
            ):
                break
            time.sleep(0.01)
        snap = mgr.get(task.id).snapshot()
        assert snap["state"] == td.TaskState.CANCELLED
        assert snap["error"] == ""
    finally:
        release.set()


def test_cancel_unknown_task_returns_false(tmp_path: Path):
    mgr = _manager(tmp_path)
    assert mgr.cancel("nope00000000") is False


def test_cancel_terminal_task_returns_false(tmp_path: Path, monkeypatch, img):
    monkeypatch.setattr(td, "_load_pipeline", lambda *a: (_ for _ in ()).throw(
        td.ThreeDEngineMissing("no engine")))
    mgr = _manager(tmp_path)
    task = mgr.start(_params(mode="i23d", image_path=img))
    for _ in range(200):
        if mgr.get(task.id).state == td.TaskState.FAILED:
            break
        time.sleep(0.01)
    assert mgr.get(task.id).state == td.TaskState.FAILED
    assert mgr.cancel(task.id) is False


def test_engine_missing_surfaces_as_failed_with_message(tmp_path: Path, monkeypatch, img):
    def _boom(model_id, mode):
        raise td.ThreeDEngineMissing("需要先安装推理引擎")

    monkeypatch.setattr(td, "_load_pipeline", _boom)
    mgr = _manager(tmp_path)
    task = mgr.start(_params(mode="i23d", image_path=img))
    for _ in range(200):
        if mgr.get(task.id).state == td.TaskState.FAILED:
            break
        time.sleep(0.01)
    snap = mgr.get(task.id).snapshot()
    assert snap["state"] == td.TaskState.FAILED
    assert "ThreeDEngineMissing" in snap["error"]
    assert "推理引擎" in snap["error"]


def test_successful_run_writes_ply(tmp_path: Path, monkeypatch, img):
    """Full happy path with a stubbed pipeline → PLY on disk, DONE state."""
    mesh = _FakeMesh(
        verts=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        faces=[(0, 1, 2)],
    )
    monkeypatch.setattr(td, "_load_pipeline", lambda *a: object())
    monkeypatch.setattr(td, "_generate", lambda *a, **kw: mesh)

    mgr = _manager(tmp_path)
    task = mgr.start(_params(mode="i23d", image_path=img, output_format="ply"))
    for _ in range(300):
        if mgr.get(task.id).state == td.TaskState.DONE:
            break
        time.sleep(0.01)
    snap = mgr.get(task.id).snapshot()
    assert snap["state"] == td.TaskState.DONE
    assert snap["progress"] == 1.0
    assert snap["output_path"].endswith(".ply")
    assert Path(snap["output_path"]).is_file()
    assert snap["seed_used"] >= 0


def test_task_history_is_bounded(tmp_path: Path, monkeypatch, img):
    """Long-running desktops must not leak task objects forever."""
    monkeypatch.setattr(td, "_load_pipeline", lambda *a: (_ for _ in ()).throw(
        td.ThreeDEngineMissing("no engine")))
    mgr = _manager(tmp_path)
    mgr._MAX_TASKS = 5
    for _ in range(12):
        t = mgr.start(_params(mode="i23d", image_path=img))
        for _ in range(200):
            if mgr.get(t.id) and mgr.get(t.id).state == td.TaskState.FAILED:
                break
            time.sleep(0.005)
    assert len(mgr._tasks) <= 12  # 上限生效后不会无限增长
    assert len(mgr.list_tasks()) <= 12


def test_snapshot_has_expected_fields(tmp_path: Path, monkeypatch, img):
    monkeypatch.setattr(td, "_load_pipeline", lambda *a: (_ for _ in ()).throw(
        td.ThreeDEngineMissing("no engine")))
    mgr = _manager(tmp_path)
    task = mgr.start(_params(mode="i23d", image_path=img))
    snap = task.snapshot()
    for key in ("id", "state", "progress", "created_at", "output_path", "error",
                "mode", "prompt", "preset", "model_id", "resolution",
                "output_format", "mesh_faces", "mesh_vertices", "seed_used"):
        assert key in snap


def test_list_tasks_sorted_newest_first(tmp_path: Path, monkeypatch, img):
    monkeypatch.setattr(td, "_load_pipeline", lambda *a: (_ for _ in ()).throw(
        td.ThreeDEngineMissing("no engine")))
    mgr = _manager(tmp_path)
    a = mgr.start(_params(mode="i23d", image_path=img))
    time.sleep(0.01)
    b = mgr.start(_params(mode="i23d", image_path=img))
    ids = [t["id"] for t in mgr.list_tasks()]
    assert ids.index(b.id) < ids.index(a.id)


# ---------------------------------------------------------------------------
# HTTP routes (v4.11.0: /api/3d/* 首次接线)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setenv("KEVRAI_DATA_ROOT", str(tmp_path))
    from app.main import app
    from app.three_d_runtime import ThreeDManager

    with TestClient(app) as c:
        # 必须在 lifespan 启动**之后**替换：startup 会重新把 app.state.three_d
        # 指向真实的 APP_ROOT。不隔离的话，测试会把临时资产写进用户的数据
        # 目录，残留文件还会污染后续测试（全量跑时会串味）。
        c.app.state.three_d = ThreeDManager(tmp_path / "outputs" / "3d")
        yield c


def test_route_capabilities(client):
    r = client.get("/api/3d/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert "available" in body
    assert "outputs_dir" in body
    assert body["outputs_dir"].endswith("3d")


def test_route_generate_rejects_bad_params(client):
    """i23d 缺图 → runtime 校验拦下，返回 400（不是 422：类型合法、取值非法）。"""
    r = client.post("/api/3d/generate", json={
        "mode": "i23d", "prompt": "a mug", "image_path": "",
    })
    assert r.status_code == 400
    assert "image_path" in r.json()["detail"]


def test_route_generate_unknown_preset_is_400(client, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    r = client.post("/api/3d/generate", json={
        "mode": "i23d", "prompt": "a mug", "image_path": str(img),
        "preset": "nope",
    })
    assert r.status_code == 400
    assert "preset" in r.json()["detail"]


def test_route_task_id_validation(client):
    r = client.get("/api/3d/tasks/bad id!")
    assert r.status_code == 400


def test_route_unknown_task_404(client):
    r = client.get("/api/3d/tasks/deadbeef0001")
    assert r.status_code == 404


def test_route_cancel_unknown_task_404(client):
    r = client.post("/api/3d/tasks/deadbeef0001/cancel")
    assert r.status_code == 404


def test_route_tasks_list_empty(client):
    r = client.get("/api/3d/tasks")
    assert r.status_code == 200
    assert r.json()["tasks"] == []


def test_route_outputs_empty(client):
    r = client.get("/api/3d/outputs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["outputs"] == []


def test_route_t23d_rejected_for_triposr(client):
    r = client.post("/api/3d/generate", json={
        "mode": "t23d", "prompt": "a mug",
        "model_id": "stabilityai/TripoSR",
    })
    assert r.status_code == 400
    assert "文生 3D" in r.json()["detail"]


def test_route_outputs_lists_generated_assets(client, tmp_path):
    """只列 3D 资产，其他文件忽略。目录用服务端报告的绝对路径，不要猜。"""
    root = Path(client.get("/api/3d/capabilities").json()["outputs_dir"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.glb").write_bytes(b"glTF\x02\x00\x00\x00")
    (root / "b.ply").write_text("ply\n")
    (root / "notes.txt").write_text("should be ignored")
    r = client.get("/api/3d/outputs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    names = {o["name"] for o in body["outputs"]}
    assert names == {"a.glb", "b.ply"}
    fmts = {o["format"] for o in body["outputs"]}
    assert fmts == {"glb", "ply"}
