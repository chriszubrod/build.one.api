import ast
from collections import Counter
from pathlib import Path


REPO_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "entities"
    / "bill"
    / "persistence"
    / "repo.py"
)


def _bill_repository_method_names() -> list[str]:
    tree = ast.parse(REPO_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "BillRepository":
            return [
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
    raise AssertionError("BillRepository class not found in repo.py")


def test_bill_repository_has_no_duplicate_method_names():
    names = _bill_repository_method_names()
    counts = Counter(names)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    assert not duplicates, (
        "BillRepository defines duplicate methods: "
        + ", ".join(duplicates)
    )
