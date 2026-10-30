import json

from agentic_sdlc.audit import AuditLog, fingerprint, redact


def test_chain_verifies_and_detects_tampering(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, "r1")
    for i in range(5):
        log.record("event", data={"i": i})
    assert AuditLog.verify(path).ok
    assert AuditLog.verify(path).records == 5

    lines = path.read_text().splitlines()
    rec = json.loads(lines[2])
    rec["data"]["i"] = 99
    lines[2] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    result = AuditLog.verify(path)
    assert not result.ok and "tampered" in result.error


def test_detects_deletion_and_reordering(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, "r1")
    for i in range(4):
        log.record("event", data={"i": i})
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2], lines[3]]) + "\n")
    assert not AuditLog.verify(path).ok
    path.write_text("\n".join([lines[1], lines[0], lines[2], lines[3]]) + "\n")
    assert not AuditLog.verify(path).ok


def test_chain_continues_across_reopen(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path, "r1").record("a")
    AuditLog(path, "r1").record("b")
    assert AuditLog.verify(path).records == 2


def test_dedupe_key_makes_emission_idempotent(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, "r1")
    assert log.record("x", dedupe_key="k") is not None
    assert log.record("x", dedupe_key="k") is None
    assert AuditLog(path, "r1").record("x", dedupe_key="k") is None
    assert len(log.events()) == 1


def test_secrets_are_redacted():
    out = redact({"msg": "key sk-ant-abcdefghijklmnop and password=hunter22222", "n": [ "AKIAABCDEFGHIJKLMNOP"]})
    text = json.dumps(out)
    assert "sk-ant-abcdef" not in text and "hunter2" not in text and "AKIAABCDEFGHIJKLMNOP" not in text
    assert "[REDACTED]" in text


def test_fingerprint_is_order_independent():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
