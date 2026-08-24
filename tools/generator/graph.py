from __future__ import annotations

from collections import defaultdict, deque

from .model_loader import Model, Table


def dependency_graph(model: Model) -> dict[str, set[str]]:
    names = {t.name for t in model.tables}
    graph: dict[str, set[str]] = {t.name: set() for t in model.tables}
    for table in model.tables:
        for fk in table.foreign_keys:
            if fk.parent_table in names and fk.parent_table != table.name:
                graph[table.name].add(fk.parent_table)
    return graph


def topological_sort(model: Model) -> list[Table]:
    graph = dependency_graph(model)
    dependents: dict[str, set[str]] = defaultdict(set)
    indegree = {name: len(parents) for name, parents in graph.items()}
    for child, parents in graph.items():
        for parent in parents:
            dependents[parent].add(child)

    ready = deque(sorted(name for name, degree in indegree.items() if degree == 0))
    ordered: list[str] = []
    while ready:
        name = ready.popleft()
        ordered.append(name)
        for child in sorted(dependents[name]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    by_name = model.by_name()
    remaining = sorted(name for name, degree in indegree.items() if degree > 0)
    return [by_name[n] for n in ordered + remaining]


def find_cycles(model: Model) -> list[list[str]]:
    graph = dependency_graph(model)
    cycles: set[tuple[str, ...]] = set()
    visiting: list[str] = []
    visited: set[str] = set()

    def dfs(node: str) -> None:
        if node in visiting:
            cycle = visiting[visiting.index(node):] + [node]
            rotations = [tuple(cycle[i:-1] + cycle[:i] + [cycle[i]]) for i in range(len(cycle) - 1)]
            cycles.add(min(rotations))
            return
        if node in visited:
            return
        visiting.append(node)
        for parent in sorted(graph.get(node, ())):
            dfs(parent)
        visiting.pop()
        visited.add(node)

    for node in sorted(graph):
        dfs(node)
    return [list(c) for c in sorted(cycles)]
