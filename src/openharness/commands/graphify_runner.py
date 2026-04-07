"""Standalone graphify rebuild runner used by background slash commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def rebuild_code_fast(base_path: Path) -> tuple[bool, str]:
    try:
        from graphify.analyze import god_nodes, surprising_connections
        from graphify.build import build_from_json
        from graphify.cluster import cluster, score_all
        from graphify.export import to_json
        from graphify.extract import extract
        from graphify.report import generate
    except Exception as exc:
        return False, f"Graphify import failed: {exc}"

    code_exts = {
        ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".c", ".h",
        ".cpp", ".hpp", ".cc", ".cs", ".swift", ".m", ".scala", ".sh", ".bash", ".zsh", ".ps1", ".sql", ".r", ".jl",
    }
    code_files = [
        p for p in base_path.rglob("*")
        if p.is_file()
        and p.suffix.lower() in code_exts
        and not any(part.startswith(".") for part in p.parts)
        and "graphify-out" not in p.parts
        and "__pycache__" not in p.parts
    ]
    if not code_files:
        return False, "No code files found - nothing to rebuild."

    try:
        extraction = extract(code_files)
    except Exception as exc:
        return False, f"Graph extraction failed: {exc}"

    allowed_types = {"code", "document", "paper", "image"}
    normalized = 0
    for node in extraction.get("nodes", []):
        if not isinstance(node, dict):
            continue
        file_type = str(node.get("file_type", "code")).lower()
        if file_type not in allowed_types:
            node["file_type"] = "document"
            normalized += 1

    try:
        graph = build_from_json(extraction)
        communities = cluster(graph)
        cohesion = score_all(graph, communities)
        gods = god_nodes(graph)
        surprises = surprising_connections(graph, communities)
        labels = {cid: f"Community {cid}" for cid in communities}
        detection = {
            "files": {"code": [str(f) for f in code_files], "document": [], "paper": [], "image": []},
            "total_files": len(code_files),
            "total_words": 0,
        }
        out = base_path / "graphify-out"
        out.mkdir(exist_ok=True)

        report = generate(
            graph,
            communities,
            cohesion,
            labels,
            gods,
            surprises,
            detection,
            {"input": 0, "output": 0},
            str(base_path),
            suggested_questions=[],
        )
        (out / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
        to_json(graph, communities, str(out / "graph.json"))
    except Exception as exc:
        return False, f"Graph rebuild failed: {exc}"

    msg = (
        f"Graph rebuilt: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, "
        f"{len(communities)} communities"
    )
    if normalized:
        msg += f" (normalized {normalized} unsupported file_type values)"
    return True, msg


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    ok, msg = rebuild_code_fast(target)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
