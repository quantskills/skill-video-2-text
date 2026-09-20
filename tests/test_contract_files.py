"""发布契约测试 —— 守住"文档、配置、代码三方一致"这条线。

这一组测试的由来：v0.1 重构时 agents/openai.yaml 有两处 YAML 语法错误
（x-model-sources 同层混用序列与映射；description 以引号开头后还有尾随文本），
文件根本无法被解析，却是靠人工检查才发现的。契约文件写错不会有任何运行时报错，
只会让下游平台静默加载失败 —— 所以必须有测试把守。
"""
import ast
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

FIVE_PLATFORMS = ["Claude Code", "Codex", "Cursor", "Hermes", "OpenClaw"]


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _is_os_environ(node) -> bool:
    """判断 AST 节点是否是 `os.environ`。"""
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def _const_str(node):
    """取出 AST 常量节点的字符串值，不是常量则返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def collect_error_codes() -> set:
    """从 scripts/*.py 完整提取错误码：
    InputValidationError("code", ...) / code = "..." / {"code": "..."} 三种写法。
    """
    codes = set()
    for path in (REPO_ROOT / "scripts").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "InputValidationError":
                if node.args and isinstance(node.args[0], ast.Constant):
                    codes.add(node.args[0].value)
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "code" for t in node.targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    codes.add(node.value.value)
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (isinstance(key, ast.Constant) and key.value == "code"
                            and isinstance(value, ast.Constant)):
                        codes.add(value.value)
    return codes


def documented_error_codes() -> set:
    skill = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    section = skill.split("## 错误码")[1].split("\n## ")[0]
    return set(re.findall(r"^\| `([a-z_]+)` \|", section, re.M))


def embedded_blocks(pattern: str):
    """产出所有 markdown 里标注了指定语言的代码块，附文件与行号。"""
    fence = re.compile(r"```" + pattern + r"[ \w-]*\n(.*?)^```", re.S | re.M)
    for md in sorted(REPO_ROOT.rglob("*.md")) + sorted((REPO_ROOT / "agents").glob("*.mdc")):
        if ".venv" in md.parts or ".git" in md.parts:
            continue
        text = md.read_text(encoding="utf-8")
        for match in fence.finditer(text):
            line = text[:match.start()].count("\n") + 1
            yield md.relative_to(REPO_ROOT), line, match.group(1)


# ---------------------------------------------------------------------------
# 契约文件可解析性
# ---------------------------------------------------------------------------

class TestContractFilesParse:
    def test_openai_yaml_is_valid(self):
        """回归：v0.1 这个文件有两处 YAML 语法错误，下游平台会静默加载失败。"""
        data = yaml.safe_load((REPO_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        assert data["openapi"] == "3.1.0"
        assert data["info"]["title"] == "skill-video-2-text"

    def test_openai_yaml_model_sources_is_mapping(self):
        """回归：x-model-sources 曾在同一层级混用序列项与映射键。"""
        data = yaml.safe_load((REPO_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        sources = data["x-model-sources"]
        assert isinstance(sources, dict), "必须是映射，不能是裸序列"
        assert sources["repository"] == "Systran/faster-whisper-base"
        assert isinstance(sources["order"], list)
        assert sources["order"][0] == "explicit_path"

    def test_config_yaml_is_valid(self):
        data = yaml.safe_load((REPO_ROOT / "config" / "transcribe_config.yaml").read_text(encoding="utf-8"))
        assert set(data) == {"model", "download", "transcribe", "input", "output"}

    def test_ci_workflow_is_valid(self):
        data = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8"))
        assert "validate" in data["jobs"]

    def test_skill_json_is_valid(self):
        data = json.loads((REPO_ROOT / "skill.json").read_text(encoding="utf-8"))
        assert data["name"] == "skill-video-2-text"

    @pytest.mark.parametrize("path,keys", [
        ("skill.json", ["name", "version", "entrypoint", "runtime", "license"]),
    ])
    def test_skill_json_required_keys(self, path, keys):
        data = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))
        assert all(k in data for k in keys), f"缺少: {set(keys) - set(data)}"

    def test_skill_json_declares_no_auth(self):
        """本 skill 的卖点之一就是零凭据，声明必须显式。"""
        data = json.loads((REPO_ROOT / "skill.json").read_text(encoding="utf-8"))
        assert data["auth_required"] is False
        assert data["offline"] is True

    def test_skill_json_runtime_matches_env_check(self):
        from scripts import env_check
        data = json.loads((REPO_ROOT / "skill.json").read_text(encoding="utf-8"))
        assert data["runtime"] == f"python>={env_check.MIN_PYTHON[0]}.{env_check.MIN_PYTHON[1]}"


class TestEmbeddedSnippetsParse:
    """文档里给用户复制的 yaml/json 代码块必须真的能解析 ——
    用户复制一段坏 YAML 到自己的 Cursor/Hermes 配置里，排查成本极高。"""

    def test_all_yaml_blocks_parse(self):
        blocks = list(embedded_blocks("ya?ml"))
        assert blocks, "文档里应该有 yaml 代码块"
        for path, line, body in blocks:
            try:
                yaml.safe_load(body)
            except Exception as exc:
                pytest.fail(f"{path}:{line} 的 yaml 代码块无法解析: {str(exc).splitlines()[0]}")

    def test_all_json_blocks_parse(self):
        blocks = list(embedded_blocks("json"))
        assert blocks, "文档里应该有 json 代码块"
        for path, line, body in blocks:
            try:
                json.loads(body)
            except Exception as exc:
                pytest.fail(f"{path}:{line} 的 json 代码块无法解析: {exc}")


# ---------------------------------------------------------------------------
# 三方一致性
# ---------------------------------------------------------------------------

class TestErrorCodeConsistency:
    def test_twelve_codes(self):
        assert len(collect_error_codes()) == 12

    def test_skill_md_matches_code(self):
        """SKILL.md 的错误码表必须与代码实际抛出的完全一致。"""
        assert collect_error_codes() == documented_error_codes()

    def test_openai_yaml_matches_code(self):
        data = yaml.safe_load((REPO_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        assert set(data["x-error-codes"]) == collect_error_codes()

    def test_every_code_is_tested(self):
        """每个错误码都要有测试断言，否则改了没人发现。"""
        tests = "\n".join(p.read_text(encoding="utf-8") for p in (REPO_ROOT / "tests").glob("*.py"))
        uncovered = sorted(c for c in collect_error_codes() if c not in tests)
        assert not uncovered, f"这些错误码没有测试覆盖: {uncovered}"

    def test_every_code_has_a_hint_in_docs(self):
        skill = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        section = skill.split("## 错误码")[1].split("\n## ")[0]
        for code in collect_error_codes():
            row = [line for line in section.splitlines() if line.startswith(f"| `{code}`")]
            assert row, f"{code} 不在 SKILL.md 错误码表里"
            cells = [c.strip() for c in row[0].strip("|").split("|")]
            assert len(cells) == 3 and cells[2], f"{code} 缺少「该怎么办」一列"


class TestExitCodeConsistency:
    def test_documented_exit_codes(self):
        skill = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        section = skill.split("### 退出码")[1].split("\n## ")[0]
        documented = set(re.findall(r"^\| `(\d+)` \|", section, re.M))
        assert documented == {"0", "1", "2", "130"}

    def test_self_check_exit_codes_documented_in_openai_yaml(self):
        """openai.yaml 里 self_check 步骤声明的退出码，必须与 env_check 实际行为一致。"""
        data = yaml.safe_load((REPO_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        step = next(s for s in data["x-steps"] if s["id"] == "self_check")
        declared = {int(k) for k in step["exit_codes"]}
        # env_check.self_check 只会产出这三种 exit_code
        assert declared == {0, 1, 2}
        for status, expected in (("ready", 0), ("blocked", 1), ("degraded", 2)):
            assert expected in declared, f"{status} 对应的退出码 {expected} 未在 openai.yaml 声明"


# ---------------------------------------------------------------------------
# 发布合规文件
# ---------------------------------------------------------------------------

class TestReleaseCompliance:
    def test_license_is_full_gplv3(self):
        """publish-agent 硬性要求：必须是完整 GPLv3 全文，不能是占位符。"""
        text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "GNU GENERAL PUBLIC LICENSE" in text
        assert "Version 3, 29 June 2007" in text
        assert len(text) > 30000, f"GPLv3 全文约 35KB，当前只有 {len(text)} 字节，疑似占位符"
        assert "GNU LESSER" not in text

    def test_skill_json_license_matches(self):
        data = json.loads((REPO_ROOT / "skill.json").read_text(encoding="utf-8"))
        assert data["license"].startswith("GPL-3.0")

    @pytest.mark.parametrize("platform", FIVE_PLATFORMS)
    def test_install_covers_platform(self, platform):
        """publish-agent 硬性要求：INSTALL.md 必须覆盖 5 个平台。"""
        text = (REPO_ROOT / "INSTALL.md").read_text(encoding="utf-8")
        assert f"## {platform}" in text, f"INSTALL.md 缺少 {platform} 章节"

    def test_install_declares_no_credentials(self):
        text = (REPO_ROOT / "INSTALL.md").read_text(encoding="utf-8")
        assert "None." in text or "no credentials" in text.lower()

    def test_readme_has_disclaimer_section(self):
        """publish-agent 硬性要求：README 必须含 ⚠️ 免责声明。"""
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "## ⚠️ 免责声明" in text
        for required in ("仅供研究与教育用途", "不保证", "风险边界", "非官方背书", "QuantSkills"):
            assert required in text, f"免责声明缺少要点: {required}"

    def test_english_readme_has_disclaimer(self):
        text = (REPO_ROOT / "README.en.md").read_text(encoding="utf-8")
        assert "## ⚠️ Disclaimer" in text
        assert "Research and educational use only" in text

    def test_changelog_follows_keep_a_changelog(self):
        text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "Keep a Changelog" in text
        assert "Semantic Versioning" in text
        assert re.search(r"^## \[\d+\.\d+\.\d+\]", text, re.M)

    def test_changelog_version_matches_skill_json(self):
        version = json.loads((REPO_ROOT / "skill.json").read_text(encoding="utf-8"))["version"]
        assert f"## [{version}]" in (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    def test_gitignore_protects_transcripts(self):
        """转录稿含用户隐私，必须被排除在版本控制之外。"""
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("output/", "*.transcript.json", "*.transcript.txt",
                        "*.timeline.txt", "*.transcript.md"):
            assert pattern in text, f".gitignore 缺少 {pattern}"

    def test_gitignore_excludes_model_weights(self):
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "*.bin" in text or "models/" in text


class TestSkillMdFrontmatter:
    @pytest.fixture
    def frontmatter(self):
        text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n"), "SKILL.md 必须以 frontmatter 开头"
        body = text.split("---\n")[1]
        return yaml.safe_load(body)

    def test_has_name(self, frontmatter):
        assert frontmatter["name"] == "skill-video-2-text"

    def test_has_description(self, frontmatter):
        assert isinstance(frontmatter["description"], str)
        assert len(frontmatter["description"]) > 50

    def test_has_tags(self, frontmatter):
        """对齐标杆 skill-dl-gnn-stock-graph：frontmatter 必须带 tags。"""
        assert isinstance(frontmatter["tags"], list)
        assert frontmatter["tags"], "tags 不能为空"

    def test_name_matches_skill_json(self, frontmatter):
        data = json.loads((REPO_ROOT / "skill.json").read_text(encoding="utf-8"))
        assert frontmatter["name"] == data["name"]

    def test_required_sections_present(self):
        text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for section in ("## 适用场景", "## 运行环境", "## 整体流程", "## 输出契约",
                        "## 错误码", "## 使用方式", "## 验收要求", "## 已知局限"):
            assert section in text, f"SKILL.md 缺少章节: {section}"


class TestQshFormDeclaration:
    def test_validator_passes(self):
        """用标杆仓库同一个校验器跑，退出码必须为 0。"""
        validator = REPO_ROOT / "scripts" / "validate-qsh-form.mjs"
        assert validator.is_file()
        proc = subprocess.run(
            ["node", str(validator), str(REPO_ROOT / "SKILL.md")],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 and "node" in (proc.stderr + proc.stdout).lower():
            pytest.skip("本机无 node，跳过 qsh-form 校验")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "校验通过" in proc.stdout

    def test_validator_is_byte_identical_to_benchmark(self):
        """校验器必须与标杆同源，否则"通过"没有意义。"""
        benchmark = (REPO_ROOT.parent / "skill-dl-gnn-stock-graph" / "scripts" / "validate-qsh-form.mjs")
        if not benchmark.is_file():
            pytest.skip("标杆仓库不在本地")
        mine = (REPO_ROOT / "scripts" / "validate-qsh-form.mjs").read_bytes()
        assert mine == benchmark.read_bytes(), "校验器与标杆不一致，请重新同步"

    def test_form_block_present(self):
        text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        assert "```json qsh-form" in text


# ---------------------------------------------------------------------------
# 目录结构完整性
# ---------------------------------------------------------------------------

class TestDirectoryContract:
    @pytest.mark.parametrize("path", [
        "SKILL.md", "README.md", "README.en.md", "LICENSE", "INSTALL.md",
        "CHANGELOG.md", "skill.json", "requirements.txt", "requirements-dev.txt",
        ".gitignore", ".github/workflows/validate.yml",
        "config/transcribe_config.yaml",
        "scripts/__init__.py", "scripts/transcribe.py", "scripts/model_utils.py",
        "scripts/env_check.py", "scripts/config.py", "scripts/validate.py",
        "scripts/validate-qsh-form.mjs",
        "tests/conftest.py",
        "references/data_guide.md", "references/methodology.md",
        "references/need_used_api.md", "references/source_boundary.md",
        "agents/cursor-rule.mdc", "agents/openai.yaml", "agents/portable-loader.md",
    ])
    def test_file_exists(self, path):
        assert (REPO_ROOT / path).is_file(), f"契约文件缺失: {path}"

    def test_pycache_is_gitignored(self):
        """跑测试必然产生 __pycache__，所以只能靠 .gitignore 挡住，不能靠"目录里没有"。"""
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "__pycache__/" in text
        assert "*.py[cod]" in text

    def test_venv_is_gitignored(self):
        assert ".venv/" in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    def test_no_stray_dev_files(self):
        """交付目录里不该有 .venv 之外的临时物。"""
        allowed_dirs = {"scripts", "tests", "references", "config", "agents", ".github", ".venv", ".pytest_cache"}
        stray = [p.name for p in REPO_ROOT.iterdir()
                 if p.is_dir() and p.name not in allowed_dirs and not p.name.startswith(".git")]
        assert not stray, f"发现计划外目录: {stray}"


# ---------------------------------------------------------------------------
# 依赖声明
# ---------------------------------------------------------------------------

class TestRequirements:
    @pytest.mark.parametrize("filename", ["requirements.txt", "requirements-dev.txt"])
    def test_parseable_by_pip_grammar(self, filename):
        """行内注释必须用「空白 + #」，否则 pip 会把它当成版本约束的一部分。"""
        from packaging.requirements import Requirement
        comment = re.compile(r"(^|\s+)#.*$")
        found = 0
        for lineno, raw in enumerate((REPO_ROOT / filename).read_text(encoding="utf-8").splitlines(), 1):
            line = comment.sub("", raw).strip()
            if not line or line.startswith("-"):
                continue
            try:
                Requirement(line)
            except Exception as exc:
                pytest.fail(f"{filename}:{lineno} 无法解析 {line!r}: {exc}")
            found += 1
        assert found >= 2, f"{filename} 至少应声明 2 个依赖"

    def test_runtime_deps_cover_hard_dependencies(self):
        """env_check 判为阻塞项的依赖，必须在 requirements.txt 里声明。"""
        from scripts import env_check
        text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for dep in env_check.HARD_DEPENDENCIES:
            assert dep.replace("_", "-") in text, f"硬依赖 {dep} 未在 requirements.txt 声明"

    def test_dev_deps_are_light(self):
        """测试依赖必须保持轻量 —— 这是"零重依赖也能验证契约"的前提。

        注意要先剥离注释：requirements-dev.txt 的说明文字里就写着
        `pip install -r requirements.txt`，那是给用户的指引，不是真实依赖声明。
        """
        raw = (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        declared = [
            re.sub(r"(^|\s+)#.*$", "", line).strip()
            for line in raw.splitlines()
        ]
        declared = [d for d in declared if d]
        assert not any(d.startswith("-r") for d in declared), \
            f"requirements-dev.txt 不应拉入运行期重依赖: {declared}"
        lowered = " ".join(declared).lower()
        assert "pytest" in lowered and "pyyaml" in lowered
        for heavy in ("faster-whisper", "ctranslate2", "modelscope", "torch"):
            assert heavy not in lowered, f"测试依赖里混入了重依赖: {heavy}"

    def test_code_reads_no_credential_env_vars(self):
        """精确断言：scripts/ 里不得从环境读取任何凭据。

        这是本 skill 相对同仓库其他 skill 的核心区别 —— 装完就能用，零账号。
        只扫代码里的环境变量访问点，不扫文档（文档里的"不读取 *_USERNAME"
        是正面声明，不是违规）。
        """
        credential = re.compile(
            r"(USERNAME|PASSWORD|API_KEY|TOKEN|SECRET|CREDENTIAL)"
        )
        accessed, offenders = set(), []
        for path in sorted((REPO_ROOT / "scripts").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                # os.environ["X"] / os.environ.get("X")
                if isinstance(node, ast.Subscript) and _is_os_environ(node.value):
                    names.append(_const_str(node.slice))
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get" and _is_os_environ(node.func.value)):
                    names.append(_const_str(node.args[0]) if node.args else None)
                # os.getenv("X")
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "getenv"):
                    names.append(_const_str(node.args[0]) if node.args else None)
                for name in names:
                    if not name:
                        continue
                    accessed.add(name)
                    if credential.search(name.upper()):
                        offenders.append(f"{path.name}: {name}")

        assert not offenders, f"代码从环境读取了凭据类变量: {offenders}"
        # 顺带确认确实只读了这几个无害变量，防止断言因为没扫到东西而空过
        assert accessed <= {"HF_ENDPOINT", "FASTER_WHISPER_MODEL_DIR", "FASTER_WHISPER_CACHE_ROOT"}, \
            f"出现了预期之外的环境变量读取: {sorted(accessed)}"

    def test_docs_declare_no_credentials(self):
        """面向用户的文档必须明说"无需凭据"，否则用户会去找不存在的配置项。"""
        for doc in ("INSTALL.md", "agents/portable-loader.md"):
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
            assert re.search(r"(无需任何账号|no credentials|Credentials[\s\S]{0,40}None)", text, re.I), \
                f"{doc} 未声明无需凭据"
