"""Higher-level system prompt assembly."""

from __future__ import annotations

import json
import re
from pathlib import Path

from openharness.config.paths import get_project_issue_file, get_project_pr_comments_file
from openharness.config.settings import Settings
from openharness.memory import find_relevant_memories, load_memory_prompt
from openharness.prompts.claudemd import load_claude_md_prompt
from openharness.prompts.system_prompt import build_system_prompt
from openharness.skills.loader import load_skill_registry


def _find_relevant_graph_context(query: str, cwd: Path, max_chars: int = 4000) -> str | None:
    """Query the graphify knowledge graph for relevant context.

    Returns a string with relevant nodes and edges, or None if no graph exists.
    """
    graph_path = cwd / "graphify-out" / "graph.json"
    if not graph_path.exists():
        return None

    try:
        import networkx as nx
        from networkx.readwrite import json_graph

        data = json.loads(graph_path.read_text(encoding="utf-8"))
        G = json_graph.node_link_graph(data, edges="links")

        # Find matching nodes
        tokens = {t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) >= 3}
        if not tokens:
            return None

        scored = []
        for nid, ndata in G.nodes(data=True):
            label = ndata.get("label", "").lower()
            score = sum(1 for t in tokens if t in label)
            if score > 0:
                scored.append((score, nid))
        scored.sort(reverse=True)
        start_nodes = [nid for _, nid in scored[:3]]

        if not start_nodes:
            return None

        # BFS to collect subgraph
        frontier = set(start_nodes)
        subgraph_nodes: set[str] = set(start_nodes)
        subgraph_edges: list[tuple[str, str]] = []

        for _ in range(2):
            if not frontier:
                break
            next_frontier: set[str] = set()
            for n in frontier:
                for neighbor in G.neighbors(n):
                    if neighbor not in subgraph_nodes:
                        next_frontier.add(neighbor)
                        subgraph_edges.append((n, neighbor))
                        subgraph_nodes.add(neighbor)
            frontier = next_frontier

        if not subgraph_nodes:
            return None

        # Render subgraph
        lines: list[str] = []
        char_count = 0

        for nid in sorted(subgraph_nodes, key=lambda n: G.degree(n), reverse=True):
            if char_count >= max_chars:
                break
            ndata = G.nodes[nid]
            label = ndata.get("label", nid)
            source = ndata.get("source_location", "")
            community = ndata.get("community", "?")
            line = f"[{community}] {label}"
            if source:
                line += f"\n  → {source}"
            if char_count + len(line) > max_chars:
                break
            lines.append(line)
            char_count += len(line) + 1

        if not lines:
            return None

        return "# Relevant Knowledge Graph Context\n\n" + "\n".join(lines)

    except Exception:
        # Silently fail - graph querying is best-effort
        return None


def _build_skills_section(cwd: str | Path) -> str | None:
    """Build a system prompt section listing available skills."""
    registry = load_skill_registry(cwd)
    skills = registry.list_skills()
    if not skills:
        return None
    lines = [
        "# Available Skills",
        "",
        "The following skills are available via the `skill` tool. "
        "When a user's request matches a skill, invoke it with `skill(name=\"<skill_name>\")` "
        "to load detailed instructions before proceeding.",
        "",
    ]
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)


def build_runtime_system_prompt(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None = None,
) -> str:
    """Build the runtime system prompt with project instructions and memory."""
    sections = [build_system_prompt(custom_prompt=settings.system_prompt, cwd=str(cwd))]

    if settings.fast_mode:
        sections.append(
            "# Session Mode\nFast mode is enabled. Prefer concise replies, minimal tool use, and quicker progress over exhaustive exploration."
        )

    sections.append(
        "# Reasoning Settings\n"
        f"- Effort: {settings.effort}\n"
        f"- Passes: {settings.passes}\n"
        "Adjust depth and iteration count to match these settings while still completing the task."
    )

    skills_section = _build_skills_section(cwd)
    if skills_section:
        sections.append(skills_section)

    claude_md = load_claude_md_prompt(cwd)
    if claude_md:
        sections.append(claude_md)

    for title, path in (
        ("Issue Context", get_project_issue_file(cwd)),
        ("Pull Request Comments", get_project_pr_comments_file(cwd)),
    ):
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"# {title}\n\n```md\n{content[:12000]}\n```")

    if settings.memory.enabled:
        memory_section = load_memory_prompt(
            cwd,
            max_entrypoint_lines=settings.memory.max_entrypoint_lines,
        )
        if memory_section:
            sections.append(memory_section)

        if latest_user_prompt:
            relevant = find_relevant_memories(
                latest_user_prompt,
                cwd,
                max_results=settings.memory.max_files,
            )
            if relevant:
                lines = ["# Relevant Memories"]
                for header in relevant:
                    content = header.path.read_text(encoding="utf-8", errors="replace").strip()
                    lines.extend(
                        [
                            "",
                            f"## {header.path.name}",
                            "```md",
                            content[:8000],
                            "```",
                        ]
                    )
                sections.append("\n".join(lines))

            # Also query the graphify knowledge graph if available
            graph_context = _find_relevant_graph_context(latest_user_prompt, Path(cwd))
            if graph_context:
                sections.append(graph_context)

    return "\n\n".join(section for section in sections if section.strip())
