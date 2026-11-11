"""Deterministic Louvain community detection (stdlib only).

Blondel et al. modularity, one or more aggregation levels. Nodes are always
visited in sorted order. Equal modularity gains do not move a node, so the
partition does not depend on hash randomization. ``seed`` is accepted and
recorded by callers; iteration order is the sorted node id, which keeps two
runs on the same edges identical.
"""

from __future__ import annotations

from collections import defaultdict


def louvain(
    nodes: list[str],
    edges: list[tuple[str, str, float]],
    *,
    resolution: float = 1.0,
    seed: int = 42,
    max_passes: int = 16,
    max_levels: int = 6,
) -> dict[str, int]:
    """Map each node to a community id (0..k-1).

    ``seed`` is part of the deterministic contract (same seed + same input →
    same partition). Visit order is sorted, so the seed does not shuffle nodes.
    """
    del seed  # sorted order is the determinism mechanism
    original = sorted({str(n) for n in nodes})
    if not original:
        return {}

    adj: dict[str, dict[str, float]] = {n: {} for n in original}
    self_w: dict[str, float] = {}
    for u0, v0, w0 in edges:
        if w0 is None:
            continue
        w = float(w0)
        if w <= 0:
            continue
        u, v = str(u0), str(v0)
        if u not in adj or v not in adj:
            continue
        if u == v:
            self_w[u] = self_w.get(u, 0.0) + w
            continue
        if u > v:
            u, v = v, u
        adj[u][v] = adj[u].get(v, 0.0) + w
        adj[v][u] = adj[v].get(u, 0.0) + w

    current_nodes: list[object] = list(original)
    current_adj: dict[object, dict[object, float]] = {n: dict(adj[n]) for n in original}
    current_self: dict[object, float] = {n: self_w.get(n, 0.0) for n in original if self_w.get(n, 0.0)}
    supernode: dict[str, object] = {n: n for n in original}

    for _level in range(max_levels):
        comm = _local_move(
            current_nodes,
            current_adj,
            current_self,
            resolution=resolution,
            max_passes=max_passes,
        )
        n_comm = len(set(comm.values()))
        supernode = {orig: comm[sup] for orig, sup in supernode.items()}
        if n_comm == len(current_nodes) or n_comm <= 1:
            break
        current_nodes, current_adj, current_self = _aggregate(
            current_nodes, current_adj, current_self, comm
        )

    groups: dict[object, list[str]] = defaultdict(list)
    for orig, cid in supernode.items():
        groups[cid].append(orig)
    order = sorted(groups, key=lambda c: (min(groups[c]), str(c)))
    remap = {c: i for i, c in enumerate(order)}
    return {orig: remap[cid] for orig, cid in supernode.items()}


def _local_move(
    nodes: list[object],
    adj: dict[object, dict[object, float]],
    self_w: dict[object, float],
    *,
    resolution: float,
    max_passes: int,
) -> dict[object, int]:
    ordered = sorted(nodes, key=lambda n: str(n))
    comm = {n: i for i, n in enumerate(ordered)}
    deg: dict[object, float] = {}
    for n in ordered:
        deg[n] = sum(adj.get(n, {}).values()) + 2.0 * float(self_w.get(n, 0.0))
    m2 = sum(deg.values())
    if m2 <= 0:
        return comm
    tot: dict[int, float] = {}
    for n in ordered:
        tot[comm[n]] = tot.get(comm[n], 0.0) + deg[n]

    for _ in range(max_passes):
        moved = False
        for n in ordered:
            c_old = comm[n]
            w_to: dict[int, float] = {}
            for nb, w in adj.get(n, {}).items():
                c = comm[nb]
                w_to[c] = w_to.get(c, 0.0) + w
            tot[c_old] = tot.get(c_old, 0.0) - deg[n]
            best_c = c_old
            best_gain = w_to.get(c_old, 0.0) - resolution * deg[n] * tot.get(c_old, 0.0) / m2
            for c, w in sorted(w_to.items(), key=lambda kv: kv[0]):
                gain = w - resolution * deg[n] * tot.get(c, 0.0) / m2
                if gain > best_gain + 1e-12:
                    best_gain = gain
                    best_c = c
            tot[best_c] = tot.get(best_c, 0.0) + deg[n]
            if best_c != c_old:
                comm[n] = best_c
                moved = True
        if not moved:
            break
    return comm


def _aggregate(
    nodes: list[object],
    adj: dict[object, dict[object, float]],
    self_w: dict[object, float],
    comm: dict[object, int],
) -> tuple[list[int], dict[int, dict[int, float]], dict[int, float]]:
    pair: dict[tuple[int, int], float] = {}
    for n in nodes:
        for m, w in adj.get(n, {}).items():
            if str(n) >= str(m) and not (str(n) == str(m)):
                # each undirected pair once; self is not stored in adj
                if str(n) > str(m):
                    continue
            if str(n) > str(m):
                continue
            a, b = comm[n], comm[m]
            if a > b:
                a, b = b, a
            pair[(a, b)] = pair.get((a, b), 0.0) + float(w)
        sw = float(self_w.get(n, 0.0))
        if sw:
            a = comm[n]
            pair[(a, a)] = pair.get((a, a), 0.0) + sw
    new_nodes = sorted(set(comm.values()))
    new_adj: dict[int, dict[int, float]] = {n: {} for n in new_nodes}
    new_self: dict[int, float] = {}
    for (a, b), w in pair.items():
        if a == b:
            new_self[a] = new_self.get(a, 0.0) + w
        else:
            new_adj[a][b] = w
            new_adj[b][a] = w
    return new_nodes, new_adj, new_self
