"""Fixed local k3s evaluation matrix and safety boundaries."""
from __future__ import annotations

from dataclasses import dataclass

NAMESPACES = {
    "boutique": "karthos-eval-boutique",
    "astronomy": "karthos-eval-astronomy",
}
SOURCE_IDS = {"trace": "traces.servicegraph", "mesh": "mesh.istio"}
KARTHOS_NAMESPACE = "karthos-system"
KARTHOS_RELEASE = "karthos"


@dataclass(frozen=True)
class Cell:
    demo: str
    source: str
    repeat: int

    @property
    def namespace(self) -> str:
        return NAMESPACES[self.demo]

    @property
    def slug(self) -> str:
        return f"r{self.repeat}-{self.demo}-{self.source}"


def cells_for_repeat(repeat: int) -> list[Cell]:
    if repeat not in (1, 2, 3):
        raise ValueError("repeat must be 1, 2 or 3")
    demos = ("boutique", "astronomy") if repeat != 2 else ("astronomy", "boutique")
    sources = ("trace", "mesh") if repeat != 2 else ("mesh", "trace")
    return [Cell(demo, source, repeat) for source in sources for demo in demos]


def source_id(mode: str) -> str:
    return SOURCE_IDS[mode]


def safe_namespace(namespace: str) -> bool:
    return namespace in NAMESPACES.values()


def selected_source(plan: dict) -> str | None:
    return (plan.get("metric_assignments") or {}).get("servicegraph.request.count")
