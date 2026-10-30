from agentic_sdlc.ambiguity import analyze


def test_vague_request_is_flagged_with_questions_and_defaults():
    report = analyze("Make our short links safer for users - we keep getting complaints about bad links.")
    terms = {a.term for a in report.ambiguities}
    assert {"safer", "bad", "acceptance"} <= terms
    assert all(a.question and a.proposed_default for a in report.ambiguities)
    assert report.unresolved


def test_answers_resolve_ambiguity():
    text = "Make our short links safer for users - we keep getting complaints about bad links."
    report = analyze(text, {"safer": "denylist", "bad": "configured", "acceptance": "HTTP 400"})
    assert not report.unresolved


def test_precise_requirement_is_not_flagged():
    report = analyze("Add maxClicks (1 to 1,000,000); after maxClicks redirects return HTTP 410 Gone.")
    assert not report.unresolved and report.has_measurable_criteria
