# -*- coding: utf-8 -*-
"""容器构建配置守卫：客户可见语料必须能被构建进镜像。

ISSUE-20260914-CORPUS-MISSING-FROM-IMAGE：
  首次在容器内执行 `scripts/import_customer_corpus.py` 时报四个语料文件"文件不存在"。
  两处原因叠加（任一存在即失败）：
    1. `Dockerfile.api` 只 COPY app/workers/scripts/migrations，**没有 COPY knowledge_corpus**
    2. `.dockerignore` 有 `*.md`，会把四个 .md 语料**全部排除**，
       即便加了 COPY 也拷不进去（需 `!knowledge_corpus/**` 例外重新包含）

这类问题属于"本地跑得好好的、容器里缺文件"，且症状是运行期报错，容易被当成脚本 bug。
故用测试固定住：构建配置必须三件齐备（COPY + 两条例外 + 例外顺序在 *.md 之后）。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERIGNORE = ROOT / ".dockerignore"
DOCKERFILE_API = ROOT / "Dockerfile.api"
CORPUS_DIR = ROOT / "knowledge_corpus"


def _dockerignore_lines() -> list[str]:
    return [line.strip() for line in
            DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")]


def test_corpus_files_exist():
    files = sorted(p.name for p in CORPUS_DIR.glob("*.md"))
    assert files, f"knowledge_corpus/ 下没有任何 .md 语料（{CORPUS_DIR}）"
    # 与导入脚本的计划保持一致，避免"有文件但没在导入计划里"
    import ast
    src = (ROOT / "scripts" / "import_customer_corpus.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    planned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) \
                        and key.value.endswith(".md"):
                    planned.add(key.value)
    missing = planned - set(files)
    assert not missing, f"导入计划里的文件在 knowledge_corpus/ 中不存在：{sorted(missing)}"


def test_dockerfile_copies_corpus():
    dockerfile = DOCKERFILE_API.read_text(encoding="utf-8")
    assert "COPY knowledge_corpus" in dockerfile, (
        "Dockerfile.api 缺少 `COPY knowledge_corpus ./knowledge_corpus` —— "
        "镜像内将没有语料，容器里执行导入脚本会报文件不存在")


def test_dockerignore_reincludes_corpus():
    lines = _dockerignore_lines()
    assert "*.md" in lines, "本测试前提是 .dockerignore 用 *.md 排除 markdown"
    assert "!knowledge_corpus" in lines, (
        ".dockerignore 缺少 `!knowledge_corpus` —— *.md 会把语料全部排除掉")
    assert "!knowledge_corpus/**" in lines, (
        ".dockerignore 缺少 `!knowledge_corpus/**` —— 仅写 `!knowledge_corpus` "
        "无法让目录内的文件重新生效")


def test_negation_comes_after_md_rule():
    """Docker 按顺序匹配，`!` 例外必须排在 `*.md` 之后才生效。"""
    lines = _dockerignore_lines()
    idx_md = lines.index("*.md")
    idx_dir = lines.index("!knowledge_corpus")
    idx_glob = lines.index("!knowledge_corpus/**")
    assert idx_dir > idx_md, "`!knowledge_corpus` 必须在 `*.md` 之后，否则不生效"
    assert idx_glob > idx_md, "`!knowledge_corpus/**` 必须在 `*.md` 之后，否则不生效"
