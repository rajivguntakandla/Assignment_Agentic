from agentic_sdlc.models import FileChange
from agentic_sdlc.policy import FileDiff, PolicyEngine

ENGINE = PolicyEngine()
EXISTING = {"src/main/resources/db/migration/V1__init.sql", "src/main/java/a/Foo.java", "pom.xml"}


def change(path, action="create"):
    return FileChange(path=path, action=action, task_id="T1", content="x")


def rules(findings):
    return {f.rule for f in findings}


def test_path_controls():
    findings = ENGINE.check_paths([
        change(".github/workflows/ci.yml"),
        change("scripts/deploy.sh"),
        change("src/main/resources/db/migration/V1__init.sql", "modify"),
        change("pom.xml", "modify"),
        change("src/main/java/a/Foo.java"),
        change("src/main/java/a/Missing.java", "modify"),
    ], EXISTING)
    assert {"CC-002-protected-path", "CC-003-path-allowlist", "CC-004-immutable-migration", "CC-005-approval-path",
            "CC-006-create-overwrite", "CC-007-modify-missing"} <= rules(findings)
    severities = {f.rule: f.severity for f in findings}
    assert severities["CC-005-approval-path"] == "require_approval"
    assert severities["CC-004-immutable-migration"] == "block"


def test_new_migration_is_allowed():
    assert ENGINE.check_paths([change("src/main/resources/db/migration/V2__x.sql")], EXISTING) == []


def test_content_rules_scan_only_added_lines():
    diffs = [
        FileDiff("src/main/java/a/Svc.java", True, [(10, 'Process p = new ProcessBuilder("ls").start();'),
                                                   (11, 'String apiKey = "abcdefghijklmnop";'),
                                                   (12, 'log.info("ip {}", request.getRemoteAddr());'),
                                                   (13, "Random r = new Random();")], 0),
        FileDiff("src/test/java/a/SvcTest.java", False, [(1, "@Test void t() {}"),
                                                        (2, "Random r = new Random();")], 0),
    ]
    findings = ENGINE.check_diff(diffs, ["src/main/java/a/Svc.java"], 1, 2)
    got = rules(findings)
    assert {"SEC-001-command-exec", "SEC-103-hardcoded-credential", "CMP-002-pii-logging",
            "SEC-006-weak-random"} <= got
    weak = [f for f in findings if f.rule == "SEC-006-weak-random"]
    assert len(weak) == 1 and weak[0].path.startswith("src/main")  # main_only rule


def test_change_control_on_diff():
    diffs = [FileDiff("src/main/java/a/Other.java", True, [(1, "int x;")], 3)]
    got = rules(ENGINE.check_diff(diffs, ["src/main/java/a/Svc.java"], tests_before=5, tests_after=4))
    assert {"CC-009-test-deletion", "CC-010-tests-required", "CC-011-scope-creep"} <= got


def test_clean_change_passes():
    diffs = [FileDiff("src/main/java/a/Svc.java", True, [(1, "return a + b;")], 1),
             FileDiff("src/test/java/a/SvcTest.java", True, [(1, "@Test void adds() {}")], 0)]
    assert ENGINE.check_diff(diffs, ["src/main/java/a/Svc.java"], 3, 4) == []


def test_classification_helpers():
    assert ENGINE.is_security_sensitive("src/main/java/io/agentic/shortener/service/UrlValidator.java")
    assert ENGINE.is_schema_path("src/main/resources/db/migration/V9__x.sql")
    assert not ENGINE.is_schema_path("src/main/java/io/agentic/shortener/web/LinkController.java")
