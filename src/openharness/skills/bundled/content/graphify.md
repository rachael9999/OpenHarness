---
name: graphify
description: Knowledge graph - build, query, explore, and export the project's graphify knowledge graph
trigger: /graphify
---

# /graphify

Build and query a persistent knowledge graph from any folder of files. The graph survives across sessions and surfaces hidden connections via community detection.

## Quick Reference

| Command | What it does |
|---------|--------------|
| `/graphify --update` | Incrementally update existing graph |
| `/graphify --cluster-only` | Rerun clustering on existing graph |
| `/graphify --watch` | Auto-rebuild on file changes |
| `/graphify --mcp` | Start MCP server for live queries |
| `/graphify query "<question>"` | BFS traversal - broad context |
| `/graphify query "<question>" --dfs` | DFS traversal - trace a path |
| `/graphify path "A" "B"` | Shortest path between two concepts |
| `/graphify explain "<node>"` | Plain-language explanation of a node |
| `/graphify stats` | Show graph statistics |
| `/graphify god` | Show most connected nodes |
| `/graphify add "<url>"` | Fetch URL and add to corpus |

## Prerequisites

```bash
# Detect the correct Python interpreter
GRAPHIFY_PYTHON=$(which graphify 2>/dev/null && head -1 "$(which graphify)" | tr -d '#!' || echo "python3")
# Test import
$GRAPHIFY_PYTHON -c "import graphify" 2>/dev/null || pip install graphifyy -q --break-system-packages
# Write interpreter path for subsequent steps
$GRAPHIFY_PYTHON -c "import sys; open('.graphify_python', 'w').write(sys.executable)"
```

**For all bash blocks below, use `$(cat .graphify_python)` instead of `python3`.**

---

## Build / Update Commands

### Incremental Update (`/graphify --update`)

Updates the graph with only new or changed files:

```bash
$(cat .graphify_python) -c "
from pathlib import Path
from graphify.watch import _rebuild_code
success = _rebuild_code(Path('.'))
if success:
    print('Graph updated successfully')
else:
    print('No code files found to rebuild - graph may be stale')
"
```

### Rerun Clustering Only (`/graphify --cluster-only`)

Reruns community detection on existing graph data:

```bash
$(cat .graphify_python) -c "
import json
from pathlib import Path
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.export import to_json, to_json as export_json

data = json.loads(Path('graphify-out/graph.json').read_text())
G = build_from_json(data)
communities = cluster(G)
cohesion = score_all(G, communities)
to_json(G, communities, 'graphify-out/graph.json')
print(f'Reclustered: {len(communities)} communities')
"
```

### Watch Mode (`/graphify --watch`)

Auto-rebuild graph when files change:

```bash
$(cat .graphify_python) -m graphify.watch .
# Press Ctrl+C to stop
```

---

## Query Commands

### BFS Query (Broad Context) - `/graphify query "<question>"`

```bash
$(cat .graphify_python) -c "
import json, re
from pathlib import Path
from networkx.readwrite import json_graph

G = json_graph.node_link_graph(json.loads(Path('graphify-out/graph.json').read_text()), edges='links')
question = 'YOUR_QUESTION_HERE'
terms = [t.lower() for t in re.findall(r'[A-Za-z0-9_]+', question) if len(t) > 3]

# Find matching nodes
scored = [(sum(1 for t in terms if t in ndata.get('label', '').lower()), nid)
          for nid, ndata in G.nodes(data=True)]
scored.sort(reverse=True)
start_nodes = [nid for _, nid in scored[:3]]

if not start_nodes:
    print('No matching nodes found for:', terms)
    raise SystemExit(0)

# BFS up to depth 3
visited, edges = set(start_nodes), []
frontier = set(start_nodes)
for _ in range(3):
    next_f = set()
    for n in frontier:
        for neighbor in G.neighbors(n):
            if neighbor not in visited:
                visited.add(neighbor)
                next_f.add(neighbor)
                edges.append((n, neighbor))
    frontier = next_f

# Render results
for nid in sorted(visited, key=lambda n: G.degree(n), reverse=True)[:40]:
    ndata = G.nodes[nid]
    src = ndata.get('source_location', '')
    print(f'[{ndata.get(\"community\",\"?\")}] {ndata.get(\"label\", nid)}' + (f' -> {src}' if src else ''))
"
```

### DFS Query (Path Tracing) - `/graphify query "<question>" --dfs`

```bash
$(cat .graphify_python) -c "
import json, re
from pathlib import Path
from networkx.readwrite import json_graph

G = json_graph.node_link_graph(json.loads(Path('graphify-out/graph.json').read_text()), edges='links')
question = 'YOUR_QUESTION_HERE'
terms = [t.lower() for t in re.findall(r'[A-Za-z0-9_]+', question) if len(t) > 3]

# Find matching nodes
scored = [(sum(1 for t in terms if t in ndata.get('label', '').lower()), nid)
          for nid, ndata in G.nodes(data=True)]
scored.sort(reverse=True)
start_nodes = [nid for _, nid in scored[:3]]

if not start_nodes:
    print('No matching nodes found for:', terms)
    raise SystemExit(0)

# DFS up to depth 6
visited, edges = set(), []
stack = [(n, 0) for n in reversed(start_nodes)]
while stack:
    node, depth = stack.pop()
    if node in visited or depth > 6:
        continue
    visited.add(node)
    for neighbor in G.neighbors(node):
        if neighbor not in visited:
            stack.append((neighbor, depth + 1))
            edges.append((node, neighbor))

# Render results
for nid in sorted(visited, key=lambda n: G.degree(n), reverse=True)[:40]:
    ndata = G.nodes[nid]
    src = ndata.get('source_location', '')
    print(f'[{ndata.get(\"community\",\"?\")}] {ndata.get(\"label\", nid)}' + (f' -> {src}' if src else ''))
"
```

### Shortest Path - `/graphify path "A" "B"`

```bash
$(cat .graphify_python) -c "
import json, re
from pathlib import Path
from networkx.readwrite import json_graph
import networkx as nx

G = json_graph.node_link_graph(json.loads(Path('graphify-out/graph.json').read_text()), edges='links')

def find_node(name):
    name_lower = name.lower()
    best, best_score = None, 0
    for nid, ndata in G.nodes(data=True):
        score = sum(1 for t in name_lower.split() if t in ndata.get('label', '').lower())
        if score > best_score:
            best_score, best = score, nid
    return best

a_name, b_name = 'CONCEPT_A', 'CONCEPT_B'
a, b = find_node(a_name), find_node(b_name)
if not a:
    print(f'Source not found: {a_name}')
elif not b:
    print(f'Target not found: {b_name}')
else:
    try:
        path = nx.shortest_path(G, a, b)
        hops = len(path) - 1
        print(f'Shortest path ({hops} hops):')
        for i, node in enumerate(path):
            ndata = G.nodes[node]
            print(f'  {\"  -> \" * i}{ndata.get(\"label\", node)}')
    except nx.NetworkXNoPath:
        print(f'No path found between {a_name} and {b_name}')
"
```

### Explain Node - `/graphify explain "<node>"`

```bash
$(cat .graphify_python) -c "
import json, re
from pathlib import Path
from networkx.readwrite import json_graph

G = json_graph.node_link_graph(json.loads(Path('graphify-out/graph.json').read_text()), edges='links')
term = 'NODE_LABEL'.lower()

# Find matching node
match = None
for nid, ndata in G.nodes(data=True):
    if term in ndata.get('label', '').lower():
        match = (nid, ndata)
        break

if not match:
    print(f'Node not found: NODE_LABEL')
    raise SystemExit(0)

nid, ndata = match
print(f'## {ndata.get(\"label\", nid)}')
print(f'**Source:** {ndata.get(\"source_file\", \"?\")} {ndata.get(\"source_location\", \"\")}')
print(f'**Type:** {ndata.get(\"file_type\", \"?\")}')
print(f'**Community:** {ndata.get(\"community\", \"?\")}')
print(f'**Connections:** {G.degree(nid)}')
print()
print('### Neighbors')
for neighbor in G.neighbors(nid)[:20]:
    nd = G.nodes[neighbor]
    ed = G.edges[nid, neighbor]
    rel = ed.get('relation', '?')
    conf = ed.get('confidence', '?')
    print(f'  - {nd.get(\"label\", neighbor)} [{rel}] [{conf}]')
"
```

### Graph Statistics - `/graphify stats`

```bash
$(cat .graphify_python) -c "
import json
from pathlib import Path
from networkx.readwrite import json_graph

G = json_graph.node_link_graph(json.loads(Path('graphify-out/graph.json').read_text()), edges='links')
confs = [d.get('confidence', 'EXTRACTED') for _, _, d in G.edges(data=True)]
total = len(confs) or 1
communities = set(ndata.get('community', '?') for _, ndata in G.nodes(data=True))
print(f'Nodes: {G.number_of_nodes()}')
print(f'Edges: {G.number_of_edges()}')
print(f'Communities: {len(communities)}')
print(f'EXTRACTED: {confs.count(\"EXTRACTED\")/total*100:.0f}%')
print(f'INFERRED: {confs.count(\"INFERRED\")/total*100:.0f}%')
print(f'AMBIGUOUS: {confs.count(\"AMBIGUOUS\")/total*100:.0f}%')
"
```

### God Nodes - `/graphify god`

```bash
$(cat .graphify_python) -c "
import json
from pathlib import Path
from networkx.readwrite import json_graph

G = json_graph.node_link_graph(json.loads(Path('graphify-out/graph.json').read_text()), edges='links')
nodes = sorted(G.nodes(), key=lambda n: G.degree(n), reverse=True)[:15]
print('God nodes (most connected):')
for i, nid in enumerate(nodes, 1):
    ndata = G.nodes[nid]
    print(f'  {i}. {ndata.get(\"label\", nid)} - {G.degree(nid)} edges')
"
```

---

## MCP Server - `/graphify --mcp`

Start the graphify MCP server for live graph queries. This exposes these tools:
- `query_graph` - BFS/DFS traversal with natural language
- `get_node` - Get details for a specific node
- `get_neighbors` - Get all connections for a node
- `get_community` - Get all nodes in a community
- `god_nodes` - Get most connected nodes
- `graph_stats` - Get graph statistics
- `shortest_path` - Find path between two concepts

```bash
# Start MCP server (runs in background)
$(cat .graphify_python) -c "from graphify.serve import serve; serve()" &

# To add to OpenHarness MCP servers:
oh mcp add graphify '$(cat .graphify_python)' -c "from graphify.serve import serve; serve()"
```

---

## Add URL to Corpus

```bash
$(cat .graphify_python) -c "
from pathlib import Path
from graphify.ingest import ingest_url
import sys

url = 'URL_TO_ADD'
try:
    ingest_url(url, Path('raw'))
    print(f'Added: {url}')
except Exception as e:
    print(f'Failed: {e}')
    sys.exit(1)
"
```

---

## Rules

- **Always check if `graphify-out/graph.json` exists** before querying - if missing, tell user to run `/graphify --update` first
- For architecture/codebase questions: query the graph first before searching raw files
- Use **BFS** for "what is X connected to?" (broad context)
- Use **DFS** for "how does X reach Y?" (path tracing)
- Quote `source_location` when citing specific facts
- If graph lacks information, say so - do not hallucinate edges
- After code modifications: suggest `/graphify --update` to keep the graph fresh
