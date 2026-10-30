"""Static reasoning over the target Java codebase (brownfield impact analysis).

Builds a type-level dependency graph from package/import/identifier references, classifies files into
architectural layers, extracts HTTP endpoints and tables, then computes the impact set of a change as the
reverse-dependency closure of the seed files.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from .models import ImpactAnalysis

_CLASS = re.compile(r"\b(?:class|interface|record|enum)\s+([A-Z]\w*)")
_PACKAGE = re.compile(r"^package\s+([\w.]+);", re.M)
_IDENT = re.compile(r"\b([A-Z][A-Za-z0-9]+)\b")
_MAPPING = re.compile(r"@(Get|Post|Put|Delete|Patch)Mapping\b(?:\(\s*(?:value\s*=\s*|path\s*=\s*)?\"([^\"]*)\")?")
_TABLE = re.compile(r"@Table\(\s*name\s*=\s*\"(\w+)\"")
_SQL_TABLE = re.compile(r"(?i)create\s+table\s+(\w+)")

LAYERS = [("web", "/web/"), ("service", "/service/"), ("repository", "/repository/"), ("domain", "/domain/"),
          ("config", "/config/"), ("ratelimit", "/ratelimit/")]


@dataclass
class JavaType:
    name: str
    path: str
    layer: str
    references: set[str] = field(default_factory=set)
    endpoints: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    stereotype: str = ""  # controller | service | repository | entity | component | ""


def _stereotype(text: str) -> str:
    if re.search(r"@(Rest)?Controller\b", text):
        return "controller"
    if "@Service" in text:
        return "service"
    if re.search(r"extends\s+\w*Repository<", text):
        return "repository"
    if "@Entity" in text:
        return "entity"
    return "component" if "@Component" in text or "@Configuration" in text else ""


class CodebaseIndex:
    def __init__(self, root: Path):
        self.root = root
        self.types: dict[str, JavaType] = {}
        self.migrations: list[str] = []
        self._build()

    def _build(self) -> None:
        main = self.root / "src" / "main" / "java"
        sources = {p: p.read_text(errors="ignore") for p in sorted(main.rglob("*.java"))} if main.exists() else {}
        for path, text in sources.items():
            rel = str(path.relative_to(self.root))
            m = _CLASS.search(text)
            if not m:
                continue
            layer = next((name for name, marker in LAYERS if marker in rel), "app")
            self.types[m.group(1)] = JavaType(m.group(1), rel, layer)
        for text in sources.values():
            m = _CLASS.search(text)
            if not m:
                continue
            t = self.types[m.group(1)]
            t.references = {i for i in _IDENT.findall(text) if i in self.types and i != t.name}
            base = ""
            rm = re.search(r"@RequestMapping\(\s*\"([^\"]*)\"\s*\)\s*(?:@\w+[^\n]*\n\s*)*public class", text)
            if rm:
                base = rm.group(1)
            for verb, sub in _MAPPING.findall(text):
                t.endpoints.append(f"{verb.upper()} {base}{sub}")
            t.tables = _TABLE.findall(text)
            t.stereotype = _stereotype(text)
        mig = self.root / "src" / "main" / "resources" / "db" / "migration"
        if mig.exists():
            self.migrations = sorted(str(p.relative_to(self.root)) for p in mig.glob("*.sql"))

    # -------------------------------------------------------------------------------------------- graph
    def reverse_deps(self) -> dict[str, set[str]]:
        rev: dict[str, set[str]] = defaultdict(set)
        for t in self.types.values():
            for ref in t.references:
                rev[ref].add(t.name)
        return rev

    def seeds_for_terms(self, terms: list[str]) -> list[str]:
        """Rank types whose name or source mentions the requirement's domain terms."""
        scores: dict[str, int] = defaultdict(int)
        lowered = [t.lower() for t in terms if t]
        for t in self.types.values():
            text = (self.root / t.path).read_text(errors="ignore").lower()
            for term in lowered:
                if term in t.name.lower():
                    scores[t.name] += 5
                scores[t.name] += min(text.count(term), 5)
        ranked = sorted((n for n, s in scores.items() if s >= 3), key=lambda n: (-scores[n], n))
        return ranked[:6]

    def impact(self, seed_types: list[str], extra_files: list[str] | None = None) -> ImpactAnalysis:
        rev = self.reverse_deps()
        seen: set[str] = set()
        queue = deque(s for s in seed_types if s in self.types)
        while queue:
            name = queue.popleft()
            if name in seen:
                continue
            seen.add(name)
            # Upstream callers can break when a type changes; config wiring is not behavioural impact.
            queue.extend(r for r in rev.get(name, ()) if self.types[r].layer != "config")
        impacted = sorted(seen, key=lambda n: (self.types[n].layer, n))
        endpoints = sorted({e for n in impacted for e in self.types[n].endpoints})
        tables = sorted({tb for n in seen | {r for n in seen for r in self.types[n].references}
                         for tb in self.types.get(n, JavaType("", "", "")).tables})
        flows = []
        for n in impacted:
            t = self.types[n]
            if t.stereotype == "controller":
                services = sorted(r for r in t.references if self.types[r].stereotype == "service")
                # services calling services (e.g. AnalyticsService -> LinkService)
                services += sorted({r for s in services for r in self.types[s].references
                                    if self.types[r].stereotype == "service"} - set(services))
                repos = sorted({r for s in services for r in self.types[s].references
                                if self.types[r].stereotype == "repository"})
                entities = sorted({tb for r in repos for e in self.types[r].references
                                   for tb in self.types[e].tables})
                hops = [n, *services, *repos] + ([f"tables({', '.join(entities)})"] if entities else [])
                flows.append(" -> ".join(hops))
        return ImpactAnalysis(
            seed_files=sorted(self.types[s].path for s in seed_types if s in self.types),
            impacted_files=sorted({self.types[n].path for n in impacted} | set(extra_files or [])),
            impacted_modules=sorted({self.types[n].layer for n in impacted}),
            affected_endpoints=endpoints,
            data_flows=flows,
            tables=tables,
        )

    def summary(self) -> dict[str, list[str]]:
        by_layer: dict[str, list[str]] = defaultdict(list)
        for t in sorted(self.types.values(), key=lambda t: t.name):
            by_layer[t.layer].append(t.name)
        return {**by_layer, "migrations": self.migrations}
