"""U-200: Labor price two-shot cent rounding parity (AggregateTimeEntryOnSubmit policy).

Canonical: cost = round2(hours * rate); price = round2(cost * (1 + markup)), half away from zero.
Matches web ``shared/money.ts`` (41k+ fixtures). Single-shot CAST-as-DECIMAL(18,2) can differ by 1 cent.

What this detector CANNOT see:
- Column-form arithmetic spelled differently from ``var * var * (1 + ISNULL``.
- Dynamic SQL (statement structure invisible after strip).
- Expressions built by string concatenation at runtime.
"""

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re

import pytest

from tests.test_sproc_nocount_shape_guard import strip_comments_and_strings

REPO_ROOT = Path(__file__).resolve().parents[1]
TIME_ENTRY_SQL = REPO_ROOT / "entities/time_entry/sql/dbo.time_entry.sql"

# Repo-relative paths exempt from single-shot labor markup detection. Only shrink.
SINGLE_SHOT_MARKUP_ALLOWLIST = frozenset()

_SINGLE_SHOT_TWO_FACTOR_RE = re.compile(
    r"@\w+\s*\*\s*@\w+\s*\*\s*\(\s*1\s*\+\s*ISNULL",
    re.IGNORECASE,
)
_REQUIRED_TWO_SHOT = {
    "parent cost": (
        r"SET\s+@ParentCostAmount\s*=\s*ROUND\s*\(\s*@ParentTotalHrs\s*\*\s*@ParentRate\s*,\s*2\s*\)\s*;"
    ),
    "parent amount": (
        r"SET\s+@ParentAmount\s*=\s*ROUND\s*\(\s*@ParentCostAmount\s*\*\s*\(\s*1\s*\+\s*ISNULL\s*\(\s*@ParentMarkup\s*,\s*0\s*\)\s*\)\s*,\s*2\s*\)\s*;"
    ),
    "line cost": (
        r"SET\s+@CostAmount\s*=\s*ROUND\s*\(\s*@TotalHours\s*\*\s*@HourlyRate\s*,\s*2\s*\)\s*;"
    ),
    "line amount": (
        r"SET\s+@TotalAmount\s*=\s*ROUND\s*\(\s*@CostAmount\s*\*\s*\(\s*1\s*\+\s*ISNULL\s*\(\s*@Markup\s*,\s*0\s*\)\s*\)\s*,\s*2\s*\)\s*;"
    ),
}


_CENT = Decimal("0.01")


def _normalize_sql_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _iter_entity_shared_sql_files():
    paths = sorted(REPO_ROOT.glob("entities/*/sql/*.sql"))
    paths.extend(sorted(REPO_ROOT.glob("shared/sql/*.sql")))
    return [
        p
        for p in paths
        if "/migrations/" not in p.relative_to(REPO_ROOT).as_posix()
    ]


def _d(value) -> Decimal:
    return Decimal(str(value))


def round2(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def labor_price_two_shot(hours, rate, markup) -> Decimal:
    """Reference implementation: explicit two ROUND(..., 2) steps."""
    m = _d(0) if markup is None else _d(markup)
    cost = round2(_d(hours) * _d(rate))
    return round2(cost * (Decimal(1) + m))


def labor_price_single_shot(hours, rate, markup) -> Decimal:
    """Legacy single expression rounded once (SQL CAST(... AS DECIMAL(18,2)) shape)."""
    m = _d(0) if markup is None else _d(markup)
    return round2(_d(hours) * _d(rate) * (Decimal(1) + m))


@pytest.mark.parametrize(
    "hours, rate, markup, expected",
    [
        (0.75, 40.18, 0.25, "37.68"),
        (4.49, 32.50, 0.50, "218.90"),
        (1.01, 32.50, 0.50, "49.25"),
        (9.69, 32.50, 0.05, "330.68"),
        (3.79, 62.50, 0.35, "319.79"),
        (-0.75, 40.18, 0.25, "-37.68"),
        (-4.49, 32.50, 0.50, "-218.90"),
        (2.00, 50.00, 0, "100.00"),
        (2.00, 50.00, None, "100.00"),
        (1.00, 10.00, 0.10, "11.00"),
    ],
    ids=[
        "canonical-0.75-40.18-0.25",
        "canonical-4.49-32.50-0.50",
        "canonical-1.01-32.50-0.50",
        "canonical-9.69-32.50-0.05",
        "canonical-3.79-62.50-0.35",
        "negative-0.75",
        "negative-4.49",
        "markup-zero",
        "markup-none",
        "agree-single-and-two-shot",
    ],
)
def test_two_shot_labor_price_fixtures(hours, rate, markup, expected):
    assert labor_price_two_shot(hours, rate, markup) == Decimal(expected)


def test_single_shot_differs_from_two_shot_on_canonical_case():
    """Fixture matrix is discriminating — policies diverge on the known cent-off case."""
    assert labor_price_single_shot(0.75, 40.18, 0.25) == Decimal("37.67")
    assert labor_price_single_shot(1.00, 10.00, 0.10) == labor_price_two_shot(
        1.00, 10.00, 0.10
    )


def test_aggregate_time_entry_sql_pins_two_shot_round_pairs():
    text = TIME_ENTRY_SQL.read_text(encoding="utf-8")
    normalized = _normalize_sql_whitespace(strip_comments_and_strings(text))
    for label, pattern in _REQUIRED_TWO_SHOT.items():
        assert re.search(pattern, normalized, re.IGNORECASE), (
            f"missing two-shot {label} ROUND"
        )


def test_repo_sql_has_no_single_shot_labor_markup_in_executable_sql():
    assert _iter_entity_shared_sql_files(), "entity/shared SQL glob matched no files?"
    for path in _iter_entity_shared_sql_files():
        relpath = path.relative_to(REPO_ROOT).as_posix()
        if relpath in SINGLE_SHOT_MARKUP_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        normalized = _normalize_sql_whitespace(strip_comments_and_strings(text))
        match = _SINGLE_SHOT_TWO_FACTOR_RE.search(normalized)
        if match:
            snippet = normalized[max(0, match.start() - 20) : match.end() + 40]
            pytest.fail(
                f"{relpath} contains single-shot labor price expression in executable SQL: "
                f"...{snippet}..."
            )
