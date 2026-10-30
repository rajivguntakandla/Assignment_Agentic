import pytest

from agentic_sdlc.codebase import CodebaseIndex
from agentic_sdlc.models import ChangeSet, Edit, FileChange
from agentic_sdlc.resilience import OutputValidationError
from agentic_sdlc.settings import REPO_ROOT
from agentic_sdlc.workspace import Workspace


@pytest.fixture
def ws(tmp_path):
    src = tmp_path / "src-project"
    (src / "src" / "main").mkdir(parents=True)
    (src / "src" / "main" / "A.java").write_text("class A {\n    int x = 1;\n}\n")
    (src / "target").mkdir()
    (src / "target" / "junk.class").write_text("binary")
    w = Workspace(tmp_path / "run", src)
    w.ensure()
    return w


def test_build_output_is_not_copied(ws):
    assert ws.files() == {"src/main/A.java"}


def test_apply_create_and_exact_once_edit(ws):
    ws.apply(ChangeSet(summary="s", changes=[
        FileChange(path="src/main/B.java", action="create", task_id="T1", content="class B {}\n"),
        FileChange(path="src/main/A.java", action="modify", task_id="T1",
                   edits=[Edit(search="int x = 1;", replace="int x = 2;")]),
    ]))
    assert "int x = 2;" in ws.read("src/main/A.java")
    patch, diffs = ws.diff()
    assert "+    int x = 2;" in patch and "b/src/main/B.java" in patch
    assert {d.path for d in diffs} == {"src/main/A.java", "src/main/B.java"}
    assert next(d for d in diffs if d.path == "src/main/A.java").added_lines == [(2, "    int x = 2;")]


def test_apply_is_all_or_nothing(ws):
    with pytest.raises(OutputValidationError, match="exactly once"):
        ws.apply(ChangeSet(summary="s", changes=[
            FileChange(path="src/main/C.java", action="create", task_id="T1", content="class C {}"),
            FileChange(path="src/main/A.java", action="modify", task_id="T1",
                       edits=[Edit(search="does not exist", replace="x")]),
        ]))
    assert ws.files() == {"src/main/A.java"}


def test_paths_cannot_escape_the_workspace(ws):
    with pytest.raises(ValueError):
        FileChange(path="../evil.java", action="create", task_id="T1", content="x")
    with pytest.raises(ValueError):
        FileChange(path="/etc/passwd", action="create", task_id="T1", content="x")


def test_snapshot_and_restore(ws):
    before = ws.snapshot("s1")
    ws.apply(ChangeSet(summary="s", changes=[
        FileChange(path="src/main/A.java", action="modify", task_id="T1", content="changed")]))
    assert ws.tree_hash(ws.root) != before
    assert ws.restore("s1") == before
    assert ws.restore("baseline") == before


def test_codebase_index_on_real_service():
    idx = CodebaseIndex(REPO_ROOT / "url-shortener")
    summary = idx.summary()
    assert "LinkService" in summary["service"] and "LinkRepository" in summary["repository"]
    endpoints = {e for t in idx.types.values() for e in t.endpoints}
    assert "POST /api/v1/links" in endpoints and "GET /api/v1/links/{code}/stats" in endpoints

    impact = idx.impact(["Link"])
    assert "src/main/java/io/agentic/shortener/service/LinkService.java" in impact.impacted_files
    assert "links" in impact.tables
    assert any(f.startswith("RedirectController -> LinkService") for f in impact.data_flows)
    assert "config" not in impact.impacted_modules  # wiring is not behavioural impact


def test_seed_ranking_finds_relevant_types():
    idx = CodebaseIndex(REPO_ROOT / "url-shortener")
    assert "UrlValidator" in idx.seeds_for_terms(["UrlValidator", "domain"])
