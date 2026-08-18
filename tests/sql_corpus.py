"""Shared repo-wide *.sql file walker, deduped out of 5 near-identical
implementations. Each caller still supplies its OWN skip-set and root — this
module only shares the walking mechanism, not any test's scan scope."""
from pathlib import Path
from typing import FrozenSet, List

DML_KEYWORDS = frozenset({"UPDATE", "INSERT", "DELETE", "MERGE"})


def iter_repo_sql_files(root: Path, *, skip_dir_names: FrozenSet[str] = frozenset()) -> List[Path]:
    """Every *.sql file under root, sorted, excluding any whose path (relative to
    root) has a directory component in skip_dir_names. Equivalent in result-set to
    os.walk with dirname pruning, or rglob+filter — callers previously used both
    styles; this keeps each caller's exact prior skip-set semantics."""
    results: List[Path] = []
    for path in sorted(root.rglob("*.sql")):
        rel_parts = path.relative_to(root).parts[:-1]
        if skip_dir_names and skip_dir_names.intersection(rel_parts):
            continue
        results.append(path)
    return results
