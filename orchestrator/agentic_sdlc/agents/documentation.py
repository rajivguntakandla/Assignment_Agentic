from __future__ import annotations

import json
import re

from ..llm import schema_instructions
from ..models import DocsBundle
from ..resilience import OutputValidationError
from .base import Agent, AgentContext, AgentResult


class DocumentationAgent(Agent):
    """Feature documentation and changelog. Writes only to the run's artifact area (never the shared workspace),
    so it can safely run in parallel with the build and the security scan; release copies the docs in."""

    name = "documentation"

    def run(self, ctx: AgentContext) -> AgentResult:
        spec, design, impl = ctx.inputs["requirements"], ctx.inputs["design"], ctx.inputs["implementation"]
        fr_ids = [fr["id"] for fr in spec["functional_requirements"]]
        user = (
            f"Specification:\n{json.dumps(spec, indent=2)}\n\nDesign:\n"
            f"{json.dumps({k: design[k] for k in ('summary', 'decisions', 'api_changes', 'schema_changes')}, indent=2)}"
            f"\n\nImplementation summary: {impl['summary']}\nFiles changed: {impl['files_changed']}\n\n"
            f"Write the feature documentation (markdown) for API consumers and maintainers. It must reference every "
            f"requirement id ({', '.join(fr_ids)}), show request/response examples, and state limitations. Also a "
            f"one-paragraph changelog entry. feature_doc_path must be docs/features/<kebab-name>.md.\n\n"
            + schema_instructions(DocsBundle)
        )

        def validate(doc: DocsBundle) -> None:
            missing = [i for i in fr_ids if i not in doc.feature_doc]
            if missing:
                raise OutputValidationError(f"feature_doc must reference requirement ids {missing}")
            if not re.fullmatch(r"docs/features/[a-z0-9-]+\.md", doc.feature_doc_path):
                raise OutputValidationError("feature_doc_path must match docs/features/<kebab-name>.md")

        bundle, provider = self.llm_or_fallback(ctx, system="You are the technical writer.", user=user,
                                                schema=DocsBundle, validate=validate,
                                                fallback=lambda: self._template(spec, design, impl))
        assert isinstance(bundle, DocsBundle)
        ctx.rt.save_artifact("docs/" + bundle.feature_doc_path, bundle.feature_doc)
        return AgentResult(bundle.model_dump(mode="json"),
                           [(f"Documented {len(fr_ids)} requirement(s) in {bundle.feature_doc_path}",
                             "docs generated from spec + design, not from code comments")], provider)

    @staticmethod
    def _template(spec: dict, design: dict, impl: dict) -> DocsBundle:
        slug = re.sub(r"[^a-z0-9]+", "-", spec["title"].lower()).strip("-")[:50] or "feature"
        lines = [f"# {spec['title']}", "", spec["intent"], "", "## Requirements", ""]
        for fr in spec["functional_requirements"]:
            lines.append(f"- **{fr['id']}** {fr['text']}")
            lines.extend(f"  - Acceptance: {ac}" for ac in fr["acceptance_criteria"])
        lines += ["", "## Design", "", design["summary"], ""]
        lines += [f"- {d['id']}: {d['decision']} - {d['rationale']}" for d in design["decisions"]]
        lines += ["", "## API changes", ""] + [f"- {a}" for a in design.get("api_changes", []) or ["None"]]
        lines += ["", "## Files changed", ""] + [f"- `{f}`" for f in impl["files_changed"]]
        lines += ["", "## Limitations", "", "Generated from templates (LLM unavailable); review wording."]
        return DocsBundle(feature_doc_path=f"docs/features/{slug}.md", feature_doc="\n".join(lines),
                          changelog_entry=f"{spec['title']}: {impl['summary']}")
