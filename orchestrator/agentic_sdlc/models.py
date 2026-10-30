"""Typed artifacts exchanged between stages. Every agent output is validated against one of these."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Decision(BaseModel):
    """One entry of decision lineage: what was decided, why, and what it was derived from."""

    id: str
    stage: str
    actor: str
    summary: str
    rationale: str = ""
    inputs: dict[str, str] = Field(default_factory=dict)  # artifact name -> fingerprint
    supersedes: str | None = None
    at: str = ""


class Ambiguity(BaseModel):
    term: str
    question: str
    proposed_default: str


class FunctionalRequirement(BaseModel):
    id: str
    text: str
    acceptance_criteria: list[str] = Field(min_length=1)


class RequirementSpec(BaseModel):
    title: str
    intent: str
    kind: Literal["greenfield", "brownfield", "ambiguous"]
    functional_requirements: list[FunctionalRequirement] = Field(min_length=1)
    non_functional_requirements: list[str] = Field(default_factory=list)
    domain_terms: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    needs_clarification: bool = False
    revision: int = 1


class Task(BaseModel):
    id: str
    title: str
    kind: Literal["code", "test", "migration", "docs", "config"]
    depends_on: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    tasks: list[Task] = Field(min_length=1)
    waves: list[list[str]] = Field(default_factory=list)
    rationale: str = ""
    revision: int = 1


class ImpactAnalysis(BaseModel):
    seed_files: list[str] = Field(default_factory=list)
    impacted_files: list[str] = Field(default_factory=list)
    impacted_modules: list[str] = Field(default_factory=list)
    affected_endpoints: list[str] = Field(default_factory=list)
    data_flows: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class DesignDecision(BaseModel):
    id: str
    decision: str
    rationale: str
    alternatives: list[str] = Field(default_factory=list)


class Design(BaseModel):
    summary: str
    decisions: list[DesignDecision] = Field(min_length=1)
    api_changes: list[str] = Field(default_factory=list)
    schema_changes: list[str] = Field(default_factory=list)
    planned_files: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    impact: ImpactAnalysis = Field(default_factory=ImpactAnalysis)
    risk_level: Literal["low", "medium", "high"] = "medium"
    requires_approval: bool = False
    revision: int = 1


class Edit(BaseModel):
    search: str = Field(min_length=1)
    replace: str


class FileChange(BaseModel):
    path: str
    action: Literal["create", "modify"]
    task_id: str
    content: str | None = None
    edits: list[Edit] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _relative(cls, v: str) -> str:
        if v.startswith(("/", "\\")) or ".." in v.replace("\\", "/").split("/"):
            raise ValueError("path must be relative and stay inside the workspace")
        return v


class ChangeSet(BaseModel):
    summary: str
    changes: list[FileChange] = Field(min_length=1)


class PolicyFinding(BaseModel):
    rule: str
    category: Literal["security", "compliance", "change_control"]
    severity: Literal["block", "require_approval", "warn"]
    message: str
    path: str | None = None
    line: int | None = None


class VerificationReport(BaseModel):
    passed: bool
    command: list[str]
    exit_code: int
    duration_s: float
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    failed_tests: list[str] = Field(default_factory=list)
    log_tail: str = ""


class DocsBundle(BaseModel):
    feature_doc_path: str
    feature_doc: str
    changelog_entry: str
    api_examples: list[str] = Field(default_factory=list)


class ApprovalRecord(BaseModel):
    gate: str
    decision: Literal["approve", "reject", "request_changes", "abort"]
    approver: str
    comment: str = ""
    acknowledged_findings: bool = False
    at: str = ""
