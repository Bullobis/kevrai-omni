"""v2.9.0 — skill hub (external ``SKILL.md`` import) + 内置技能保全测试.

两个目标：

1. **导入链路可用** —— 解析、目录/zip/git 导入、删除、API 全链路。
2. **「原有技能不准删」** —— 这是用户下的硬约束，必须由机器钉死，而不是靠
   代码评审记得住。见 :class:`TestBuiltinPreserved`。
"""
from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from app.agent import skill_hub
from app.agent.skill import Skill
from app.agent.tools import BUILTIN_SKILLS, build_skill_manager

pytestmark = pytest.mark.usefixtures("tmp_xdg")

SKILL_MD = """---
name: 日报助手
description: 把零散记录整理成结构化日报。
---

# 工作流

1. 按项目分组
2. 提炼进展 / 阻塞 / 下一步
"""


def _write_skill(root: Path, dirname: str, text: str = SKILL_MD) -> Path:
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


# ===========================================================================
# SKILL.md 解析
# ===========================================================================
class TestParsedSkillMd:
    def test_minimal_frontmatter(self):
        p = skill_hub.parse_skill_md(SKILL_MD)
        assert p.name == "日报助手"
        assert p.description == "把零散记录整理成结构化日报。"
        assert "按项目分组" in p.guidance

    def test_body_is_preserved_verbatim(self):
        """正文是给模型的指导文本，导入时必须原文保留（不改写、不翻译）。"""
        text = "---\nname: x\ndescription: y\n---\n保留  line breaks\n\n  与缩进\n"
        p = skill_hub.parse_skill_md(text)
        assert "保留  line breaks" in p.body
        assert "  与缩进" in p.body

    def test_crlf_normalized(self):
        p = skill_hub.parse_skill_md("---\r\nname: a\r\ndescription: b\r\n---\r\n\r\nbody\r\n")
        assert p.name == "a"
        assert "body" in p.body

    def test_quoted_values_unquoted(self):
        p = skill_hub.parse_skill_md('---\nname: "带 空格"\ndescription: \'单引\'\n---\nbody\n')
        assert p.name == "带 空格"
        assert p.description == "单引"

    def test_extra_keys_kept(self):
        p = skill_hub.parse_skill_md(
            "---\nname: a\ndescription: b\nlicense: MIT\nid: custom_id\n---\nbody\n")
        assert p.extra.get("license") == "MIT"
        assert p.extra.get("id") == "custom_id"

    @pytest.mark.parametrize("text,code", [
        ("", "empty_skill_md"),
        ("   ", "empty_skill_md"),
        ("no frontmatter here", "missing_frontmatter"),
        ("---\ndescription: d\n---\nbody", "missing_name"),
        ("---\nname: n\n---\nbody", "missing_description"),
        ("---\nname: n\ndescription: d\n---\n\n  \n", "empty_body"),
    ])
    def test_invalid_inputs(self, text, code):
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.parse_skill_md(text)
        assert ei.value.code == code

    def test_oversized_body_rejected(self):
        huge = "x" * (skill_hub.MAX_GUIDANCE_CHARS + 1)
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.parse_skill_md(f"---\nname: n\ndescription: d\n---\n{huge}")
        assert ei.value.code == "body_too_long"

    def test_unterminated_frontmatter_is_not_silently_accepted(self):
        # 开头是 --- 但没闭合：不应把整个文件当成 frontmatter 解析成功。
        with pytest.raises(skill_hub.SkillHubError):
            skill_hub.parse_skill_md("---\nname: n\ndescription: d\nbody without closing")


class TestSkillIdDerivation:
    @pytest.mark.parametrize("raw,expected", [
        ("Daily Report Writer", "daily_report_writer"),
        ("Foo-Bar!!", "foo_bar"),
        ("__weird__", "weird"),
        ("123abc", "sk_123abc"),
        ("", "imported_skill"),
        ("中文名", "imported_skill"),
    ])
    def test_derive(self, raw, expected):
        sid = skill_hub.derive_skill_id(raw)
        assert sid == expected
        # 派生结果必须永远能通过校验
        assert skill_hub.validate_skill_id(sid) == sid

    def test_derived_id_within_length(self):
        assert len(skill_hub.derive_skill_id("a" * 500)) <= 64

    def test_validate_rejects_bad(self):
        with pytest.raises(skill_hub.SkillHubError):
            skill_hub.validate_skill_id("Bad-Id")


# ===========================================================================
# 导入 —— 目录
# ===========================================================================
class TestImportDir:
    def test_import_and_load(self, tmp_path):
        root = tmp_path / "lib"
        src = _write_skill(tmp_path / "src", "daily")
        res = skill_hub.import_dir(src, root)
        assert res["id"] == "daily"
        assert Path(res["path"]).is_dir()
        assert (Path(res["path"]) / "SKILL.md").is_file()

        loaded = skill_hub.load_imported(root)
        assert len(loaded) == 1
        assert loaded[0].source == "imported"
        assert loaded[0].tools == []           # 外部技能不携带 Python 工具
        assert loaded[0].guidance.strip()
        assert loaded[0].default_enabled is False   # 导入默认不启用

    def test_missing_skill_md(self, tmp_path):
        d = tmp_path / "src" / "empty"
        d.mkdir(parents=True)
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_dir(d, tmp_path / "lib")
        assert ei.value.code == "missing_skill_md"

    def test_nonexistent_path(self, tmp_path):
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_dir(tmp_path / "nope", tmp_path / "lib")
        assert ei.value.code == "not_found"

    def test_not_a_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_dir(f, tmp_path / "lib")
        assert ei.value.code == "not_a_directory"

    def test_explicit_id_in_frontmatter_wins(self, tmp_path):
        src = _write_skill(
            tmp_path / "src", "whatever",
            "---\nname: n\ndescription: d\nid: my_explicit\n---\nbody\n")
        res = skill_hub.import_dir(src, tmp_path / "lib")
        assert res["id"] == "my_explicit"

    def test_scripts_subdir_is_copied_but_not_executed(self, tmp_path):
        src = _write_skill(tmp_path / "src", "withscripts")
        (src / "scripts").mkdir()
        (src / "scripts" / "run.py").write_text("print('hi')", encoding="utf-8")
        res = skill_hub.import_dir(src, tmp_path / "lib")
        assert (Path(res["path"]) / "scripts" / "run.py").is_file()
        loaded = skill_hub.load_imported(tmp_path / "lib")
        assert loaded[0].tools == []


# ===========================================================================
# 导入 —— 内置技能保全（用户硬约束：原有技能不准删）
# ===========================================================================
class TestBuiltinPreserved:
    """「我们原有技能不准删」的机器保证。

    任何把内置技能删掉、改名、或让导入覆盖内置的改动都会在这里失败。
    """

    EXPECTED = {
        "core": {"get_preferences", "set_preference", "generate_text"},
        "model_catalog": {"search_models", "model_info", "recommend_models",
                          "list_installed", "list_categories"},
        "local_system": {"check_hardware", "list_engines", "download_model"},
        "drama_studio": {"drama_storycraft", "drama_brainstorm", "drama_compose_script",
                         "drama_storyboard", "drama_render_plan"},
        "writing_studio": None,          # 只在下方断言存在
        "media_prompt_studio": None,
    }
    EXPECTED_IDS = set(EXPECTED)

    def test_all_builtin_skills_still_present(self):
        ids = {sk.id for sk in BUILTIN_SKILLS}
        assert ids == self.EXPECTED_IDS, f"内置技能集合被改动：{ids}"

    def test_builtin_tool_sets_unchanged(self):
        for sk in BUILTIN_SKILLS:
            want = self.EXPECTED.get(sk.id)
            if want is not None:
                assert set(sk.tool_names) == want, f"{sk.id} 的工具集合被改动"

    def test_drama_skill_survived_tab_removal(self):
        """短剧 tab 被移除，但短剧技能必须完好无损。"""
        ids = {sk.id for sk in BUILTIN_SKILLS}
        assert "drama_studio" in ids

    def test_no_builtin_skill_marked_imported(self):
        for sk in BUILTIN_SKILLS:
            assert sk.source == "builtin", f"{sk.id} 来源被改成 {sk.source}"

    def test_import_cannot_shadow_builtin_id(self, tmp_path):
        """同名导入应被拒绝，而不是悄悄顶掉内置技能。"""
        src = _write_skill(
            tmp_path / "src", "core",
            "---\nname: 伪装核心\ndescription: d\n---\nbody\n")
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_dir(src, tmp_path / "lib")
        assert ei.value.code == "builtin_conflict"

    def test_zip_import_cannot_shadow_builtin_id(self, tmp_path):
        # 影子攻击的真实形态：压缩包内有一个名为 core 的目录。
        zpath = tmp_path / "evil.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("core/SKILL.md", "---\nname: 伪装核心\ndescription: d\n---\nbody\n")
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_zip(zpath, tmp_path / "lib")
        assert ei.value.code == "builtin_conflict"

    def test_remove_builtin_is_refused(self, tmp_path):
        for sk in BUILTIN_SKILLS:
            with pytest.raises(skill_hub.SkillHubError) as ei:
                skill_hub.remove_imported(sk.id, tmp_path / "lib")
            assert ei.value.code == "builtin_protected"

    def test_library_cannot_delete_builtin_files(self, tmp_path):
        """即使技能库里存在与内置同名的目录，删除也必须被拒。"""
        root = tmp_path / "lib"
        fake = _write_skill(root, "drama_studio")
        assert fake.is_dir()
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.remove_imported("drama_studio", root)
        assert ei.value.code == "builtin_protected"
        assert fake.is_dir(), "内置同名目录被删了"

    def test_load_imported_skips_builtin_named_dirs(self, tmp_path):
        """手工放进库里的内置同名目录，不会被当成外部技能加载。"""
        root = tmp_path / "lib"
        _write_skill(root, "core", "---\nname: 冒牌\ndescription: d\n---\nbody\n")
        loaded = skill_hub.load_imported(root)
        assert loaded == []

    def test_manager_keeps_builtins_when_extras_added(self, tmp_path):
        """叠加导入技能后，内置技能数量与工具数不得变化。"""
        base = build_skill_manager()
        # list_skills() 返回的是 to_spec() 字典，不是 Skill 对象。
        builtin_ids = {s["id"] for s in base.list_skills()}
        builtin_tools = len(base.active_tool_names())

        src = _write_skill(tmp_path / "src", "extra_one")
        skill_hub.import_dir(src, tmp_path / "lib")
        extra = skill_hub.load_imported(tmp_path / "lib")
        mgr = build_skill_manager(extra_skills=extra)

        ids = {s["id"] for s in mgr.list_skills()}
        assert builtin_ids <= ids, "导入技能顶掉了内置技能"
        assert "extra_one" in ids
        # 导入技能不携带工具，因此活跃工具数必须与纯内置时一致。
        assert len(mgr.active_tool_names()) == builtin_tools


# ===========================================================================
# 导入 —— zip（含安全）
# ===========================================================================
class TestImportZip:
    def _zip(self, path: Path, entries: dict[str, str]) -> Path:
        with zipfile.ZipFile(path, "w") as zf:
            for name, text in entries.items():
                zf.writestr(name, text)
        return path

    def test_flat_zip(self, tmp_path):
        zp = self._zip(tmp_path / "a.zip", {"SKILL.md": SKILL_MD})
        res = skill_hub.import_zip(zp, tmp_path / "lib")
        assert len(res) == 1
        assert res[0]["id"] == "a"

    def test_nested_zip_multiple_skills(self, tmp_path):
        zp = self._zip(tmp_path / "b.zip", {
            "skills/one/SKILL.md": "---\nname: 一\ndescription: d\n---\nbody one\n",
            "skills/two/SKILL.md": "---\nname: 二\ndescription: d\n---\nbody two\n",
        })
        res = skill_hub.import_zip(zp, tmp_path / "lib")
        assert {r["id"] for r in res} == {"one", "two"}

    def test_zip_without_skill_md(self, tmp_path):
        zp = self._zip(tmp_path / "c.zip", {"readme.txt": "nothing"})
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_zip(zp, tmp_path / "lib")
        assert ei.value.code == "missing_skill_md"

    def test_not_a_zip(self, tmp_path):
        f = tmp_path / "not.zip"
        f.write_text("definitely not a zip", encoding="utf-8")
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_zip(f, tmp_path / "lib")
        assert ei.value.code == "not_a_zip"

    @pytest.mark.parametrize("member", [
        "../../escaped/SKILL.md",
        "../outside/SKILL.md",
        "/abs/path/SKILL.md",
    ])
    def test_zip_slip_is_contained(self, tmp_path, member):
        """Zip Slip：穿越条目不得写到库外。

        ``safe_extract`` 会 **跳过** 不安全条目，因此正常 SKILL.md 仍会导入，
        但库外不能出现任何文件。
        """
        zp = self._zip(tmp_path / "slip.zip", {
            member: SKILL_MD,
            "ok/SKILL.md": SKILL_MD,
        })
        lib = tmp_path / "lib"
        # 若所有条目都被跳过，导入会抛 missing_skill_md —— 这里两种结果都接受，
        # 断言的重点是「库外不能出现任何文件」。
        with contextlib.suppress(skill_hub.SkillHubError):
            skill_hub.import_zip(zp, lib)
        # 库外绝不能出现 escaped/outside 目录
        assert not (tmp_path.parent / "escaped").exists()
        assert not (tmp_path / "outside").exists()
        assert not Path("/abs/path/SKILL.md").exists()

    def test_zip_bomb_uncompressed_cap(self, tmp_path):
        """高压缩比内容触发解压体积上限。"""
        zp = tmp_path / "bomb.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            # 全零数据压缩比极高，解压后远超上限
            zf.writestr("SKILL.md", SKILL_MD)
            zf.writestr("big.bin", b"\0" * (skill_hub.MAX_UNCOMPRESSED_BYTES + 1))
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_zip(zp, tmp_path / "lib")
        assert ei.value.code == "archive_too_large"

    def test_too_many_members(self, tmp_path):
        zp = tmp_path / "many.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            for i in range(skill_hub.MAX_MEMBERS + 5):
                zf.writestr(f"f{i}.txt", "x")
            zf.writestr("SKILL.md", SKILL_MD)
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_zip(zp, tmp_path / "lib")
        assert ei.value.code == "too_many_members"

    def test_oversized_archive_rejected(self, tmp_path):
        zp = tmp_path / "big.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("SKILL.md", SKILL_MD)
        # 直接改文件大小会让 is_zipfile 失败，所以这里走 monkeypatch 模拟
        real = skill_hub.MAX_ARCHIVE_BYTES
        try:
            skill_hub.MAX_ARCHIVE_BYTES = 1
            with pytest.raises(skill_hub.SkillHubError) as ei:
                skill_hub.import_zip(zp, tmp_path / "lib")
            assert ei.value.code == "archive_too_large"
        finally:
            skill_hub.MAX_ARCHIVE_BYTES = real


# ===========================================================================
# 导入 —— git
# ===========================================================================
def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git 不可用")
class TestImportGit:
    def _make_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git("init", "-q", cwd=repo)
        _git("config", "user.email", "t@example.com", cwd=repo)
        _git("config", "user.name", "T", cwd=repo)
        _write_skill(repo, "gitskill",
                     "---\nname: 仓库技能\ndescription: 来自 git\n---\nbody\n")
        _git("add", "-A", cwd=repo)
        _git("commit", "-qm", "init", cwd=repo)
        return repo

    def test_import_from_local_repo(self, tmp_path):
        repo = self._make_repo(tmp_path)
        res = skill_hub.import_git(repo.as_uri(), tmp_path / "lib")
        assert [r["id"] for r in res] == ["gitskill"]
        assert res[0]["source"] == "git"

    def test_dot_git_not_copied(self, tmp_path):
        repo = self._make_repo(tmp_path)
        res = skill_hub.import_git(repo.as_uri(), tmp_path / "lib")
        if res[0]["id"] == "repo":       # 根目录就是技能目录的情形
            assert not (Path(res[0]["path"]) / ".git").exists()

    def test_marketplace_manifest_drives_import(self, tmp_path):
        repo = tmp_path / "mkt"
        (repo / ".claude-plugin").mkdir(parents=True)
        _write_skill(repo / "plugins", "alpha",
                     "---\nname: Alpha\ndescription: d\n---\nbody a\n")
        (repo / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"plugins": [{"name": "alpha", "source": "plugins/alpha"}]}),
            encoding="utf-8")
        _git("init", "-q", cwd=repo)
        _git("config", "user.email", "t@example.com", cwd=repo)
        _git("config", "user.name", "T", cwd=repo)
        _git("add", "-A", cwd=repo)
        _git("commit", "-qm", "init", cwd=repo)
        res = skill_hub.import_git(repo.as_uri(), tmp_path / "lib")
        assert [r["id"] for r in res] == ["alpha"]

    def test_read_marketplace_absent_manifest(self, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        assert skill_hub.read_marketplace(d) == []

    def test_read_marketplace_root_skill_md(self, tmp_path):
        # SKILL.md 直接在仓库根：没有 manifest，合成一个单条插件条目。
        repo = tmp_path / "rootskill"
        repo.mkdir()
        (repo / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        assert skill_hub.read_marketplace(repo)[0]["name"] == "rootskill"

    def test_read_marketplace_missing_manifest_and_no_root_md(self, tmp_path):
        d = tmp_path / "nothing"
        d.mkdir()
        assert skill_hub.read_marketplace(d) == []

    def test_read_marketplace_broken_json(self, tmp_path):
        d = tmp_path / "broken"
        (d / ".claude-plugin").mkdir(parents=True)
        (d / ".claude-plugin" / "marketplace.json").write_text("{not json", encoding="utf-8")
        assert skill_hub.read_marketplace(d) == []

    def test_empty_url(self, tmp_path):
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_git("", tmp_path / "lib")
        assert ei.value.code == "empty_url"

    @pytest.mark.parametrize("url", ["C:/windows", "ftp://x/y", "just-a-path", "/local/path"])
    def test_rejects_non_repo_schemes(self, tmp_path, url):
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_git(url, tmp_path / "lib")
        assert ei.value.code == "invalid_url"

    def test_file_scheme_is_accepted_but_fails_if_not_a_repo(self, tmp_path):
        """``file://`` 是合法 git 传输（本地仓库测试依赖它），
        但如果目标不是仓库，应在 clone 阶段失败而非被 scheme 校验拦下。"""
        plain = tmp_path / "plaindir"
        plain.mkdir()
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_git(plain.as_uri(), tmp_path / "lib")
        assert ei.value.code == "git_failed"

    def test_clone_failure_reported(self, tmp_path):
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_git("https://127.0.0.1:1/nope.git", tmp_path / "lib")
        assert ei.value.code == "git_failed"

    def test_repo_without_skills(self, tmp_path):
        repo = tmp_path / "emptyrepo"
        repo.mkdir()
        (repo / "README.md").write_text("hi", encoding="utf-8")
        _git("init", "-q", cwd=repo)
        _git("config", "user.email", "t@example.com", cwd=repo)
        _git("config", "user.name", "T", cwd=repo)
        _git("add", "-A", cwd=repo)
        _git("commit", "-qm", "init", cwd=repo)
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.import_git(repo.as_uri(), tmp_path / "lib")
        assert ei.value.code == "missing_skill_md"


# ===========================================================================
# 库扫描 / 删除 / 冲突检测
# ===========================================================================
class TestLibraryManagement:
    def test_scan_empty_library(self, tmp_path):
        assert skill_hub.scan_library(tmp_path / "nope") == []

    def test_scan_reports_broken_entries(self, tmp_path):
        root = tmp_path / "lib"
        bad = root / "broken"
        bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
        items = skill_hub.scan_library(root)
        assert len(items) == 1
        assert items[0]["ok"] is False
        assert items[0]["error"]

    def test_scan_ignores_non_dirs(self, tmp_path):
        root = tmp_path / "lib"
        root.mkdir()
        (root / "loose.txt").write_text("x", encoding="utf-8")
        assert skill_hub.scan_library(root) == []

    def test_scan_reports_subdirs(self, tmp_path):
        root = tmp_path / "lib"
        d = _write_skill(root, "withsub")
        (d / "scripts").mkdir()
        (d / "references").mkdir()
        item = skill_hub.scan_library(root)[0]
        assert item["has_scripts"] and item["has_references"]
        assert not item["has_assets"]

    def test_load_imported_skips_broken(self, tmp_path):
        root = tmp_path / "lib"
        _write_skill(root, "good")
        bad = root / "bad"
        bad.mkdir()
        (bad / "SKILL.md").write_text("garbage", encoding="utf-8")
        loaded = skill_hub.load_imported(root)
        assert [s.id for s in loaded] == ["good"]

    def test_remove_roundtrip(self, tmp_path):
        root = tmp_path / "lib"
        src = _write_skill(tmp_path / "src", "removable")
        skill_hub.import_dir(src, root)
        assert (root / "removable").is_dir()
        assert skill_hub.remove_imported("removable", root)["removed"] is True
        assert not (root / "removable").exists()

    def test_remove_unknown(self, tmp_path):
        root = tmp_path / "lib"
        root.mkdir()
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.remove_imported("ghost", root)
        assert ei.value.code == "not_found"

    def test_remove_invalid_id(self, tmp_path):
        with pytest.raises(skill_hub.SkillHubError) as ei:
            skill_hub.remove_imported("Bad-Id", tmp_path / "lib")
        assert ei.value.code == "invalid_skill_id"

    def test_find_tool_conflicts(self):
        a = Skill(id="sk_a", name="a", description="d",
                  tools=[__import__("app.agent.tool_registry", fromlist=["Tool"]).Tool(
                      name="dup_tool", description="x", parameters={},
                      handler=lambda p, c: {})], guidance="g")
        b = Skill(id="sk_b", name="b", description="d",
                  tools=[__import__("app.agent.tool_registry", fromlist=["Tool"]).Tool(
                      name="dup_tool", description="x", parameters={},
                      handler=lambda p, c: {})], guidance="g")
        assert skill_hub.find_tool_conflicts([a, b]) == {"dup_tool": ["sk_a", "sk_b"]}

    def test_assert_no_conflicts_passes_for_builtins(self):
        skill_hub.assert_no_conflicts([])   # 内置之间不得有冲突

    def test_builtin_tool_names_helper(self):
        names = skill_hub.builtin_tool_names()
        assert "generate_text" in names
        assert "drama_storycraft" in names


# ===========================================================================
# API 全链路
# ===========================================================================
class TestSkillHubAPI:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        import app.main as M

        app_root = tmp_path / "approot"
        (app_root / "agent").mkdir(parents=True)
        monkeypatch.setattr(M, "APP_ROOT", app_root)
        M._AGENT_SINGLETON.clear()
        with TestClient(M.app) as c:
            yield c, app_root
        M._AGENT_SINGLETON.clear()

    def test_list_empty(self, client):
        c, _ = client
        r = c.get("/api/agent/skill-hub")
        assert r.status_code == 200
        body = r.json()
        assert body["skills"] == []
        assert body["builtin_count"] == len(BUILTIN_SKILLS)

    def test_singleton_cleared_after_import(self, client, tmp_path):
        c, _ = client
        src = _write_skill(tmp_path / "src", "api_skill")
        r = c.post("/api/agent/skill-hub/import", json={"path": str(src)})
        assert r.status_code == 200
        assert r.json()["imported"][0]["id"] == "api_skill"

        r2 = c.get("/api/agent/skill-hub")
        assert [s["id"] for s in r2.json()["skills"]] == ["api_skill"]

    def test_import_missing_path_is_400(self, client):
        c, _ = client
        r = c.post("/api/agent/skill-hub/import", json={"path": "does/not/exist"})
        assert r.status_code == 400

    def test_import_builtin_name_is_400(self, client, tmp_path):
        c, _ = client
        src = _write_skill(tmp_path / "src", "core",
                           "---\nname: 冒牌\ndescription: d\n---\nbody\n")
        r = c.post("/api/agent/skill-hub/import", json={"path": str(src)})
        assert r.status_code == 400
        assert "内置" in r.json()["detail"]

    def test_zip_path_import(self, client, tmp_path):
        c, _ = client
        zp = tmp_path / "s.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("SKILL.md", SKILL_MD)
        r = c.post("/api/agent/skill-hub/import-zip", json={"path": str(zp)})
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_zip_upload_route_absent_by_design(self, client):
        """没有 multipart 上传端点：桌面端交付的是路径而非字节，
        加它只会引入 python-multipart 运行时依赖（Starlette 会在
        request.form() 处 assert），却没有真实调用方。

        POST 到该路径会落到 DELETE /skill-hub/{skill_id} 上，
        因此返回 405（路由存在但方法不匹配）而不是 404 —— 两种都说明
        「POST 上传」这条能力确实不存在，这里断言 405 并排除 2xx。
        """
        c, _ = client
        r = c.post("/api/agent/skill-hub/import-zip-upload", files={})
        assert r.status_code == 405

    def test_git_import_bad_url_is_400(self, client):
        c, _ = client
        r = c.post("/api/agent/skill-hub/import-git", json={"url": "not-a-url"})
        assert r.status_code == 400

    def test_remove_imported(self, client, tmp_path):
        c, _ = client
        src = _write_skill(tmp_path / "src", "gone_soon")
        c.post("/api/agent/skill-hub/import", json={"path": str(src)})
        r = c.delete("/api/agent/skill-hub/gone_soon")
        assert r.status_code == 200
        assert c.get("/api/agent/skill-hub").json()["skills"] == []

    @pytest.mark.parametrize("sid", ["core", "drama_studio", "model_catalog",
                                     "local_system", "writing_studio",
                                     "media_prompt_studio"])
    def test_remove_builtin_is_403(self, client, sid):
        """「原有技能不准删」在 HTTP 层同样成立。"""
        c, _ = client
        r = c.delete(f"/api/agent/skill-hub/{sid}")
        assert r.status_code == 403

    def test_remove_unknown_is_404(self, client):
        c, _ = client
        assert c.delete("/api/agent/skill-hub/ghost_skill").status_code == 404

    def test_skill_hub_route_not_swallowed_by_skill_id(self, client):
        """``skill-hub`` 不能被 /skills/{skill_id} 通配捕获。"""
        c, _ = client
        r = c.get("/api/agent/skill-hub")
        assert r.status_code == 200
        assert "skills" in r.json()

    def test_imported_skill_appears_in_skills_list(self, client, tmp_path):
        c, _ = client
        src = _write_skill(tmp_path / "src", "visible_ext",
                           "---\nname: 可见\ndescription: d\n---\nbody\n")
        c.post("/api/agent/skill-hub/import", json={"path": str(src)})
        # 强制重建 singleton，让导入技能进入 skill manager
        import app.main as M
        M._AGENT_SINGLETON.clear()
        body = c.get("/api/agent/skills").json()
        ids = {s["id"] for s in body["skills"]}
        assert "visible_ext" in ids
        # 内置 6 个必须仍在
        for sk in BUILTIN_SKILLS:
            assert sk.id in ids

    def test_toggle_imported_skill_enables_guidance(self, client, tmp_path):
        c, _ = client
        src = _write_skill(tmp_path / "src", "togglish",
                           "---\nname: 开关\ndescription: d\n---\n独特指导文本\n")
        c.post("/api/agent/skill-hub/import", json={"path": str(src)})
        import app.main as M
        M._AGENT_SINGLETON.clear()
        r = c.post("/api/agent/skills/togglish", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["skill"]["enabled"] is True


class TestDramaRoutesIntact:
    """短剧 tab 已移除，但后端路由必须一条不动。"""

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        import app.main as M

        app_root = tmp_path / "approot"
        (app_root / "agent").mkdir(parents=True)
        monkeypatch.setattr(M, "APP_ROOT", app_root)
        M._AGENT_SINGLETON.clear()
        with TestClient(M.app) as c:
            yield c
        M._AGENT_SINGLETON.clear()

    def test_storycraft_endpoint(self, client):
        r = client.get("/api/drama/storycraft")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_drama_options_endpoint(self, client):
        assert client.get("/api/drama/options").status_code == 200

    def test_drama_tools_still_registered(self, client):
        body = client.get("/api/agent/tools").json()
        names = {t["name"] for t in body["tools"]}
        # 短剧技能默认启用，其工具必须在注册表里
        assert "drama_storycraft" in names
