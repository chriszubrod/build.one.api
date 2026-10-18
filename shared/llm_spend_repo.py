"""Persistence for dbo.LlmDailySpend — Claude (and future provider) daily totals."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pyodbc

from shared.database import call_procedure, get_connection, map_database_error


class LlmSpendRepository:
    """Calls sprocs in shared/sql/dbo.llm_daily_spend.sql."""

    def read_daily_spend(self, spend_date: date, provider: str) -> Decimal:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor,
                    "ReadLlmDailySpend",
                    {"SpendDate": spend_date, "Provider": provider},
                )
                row = cursor.fetchone()
                if not row:
                    return Decimal("0")
                return Decimal(str(row.CostUsd))
        except Exception as error:
            raise map_database_error(error)

    def add_daily_spend(
        self, spend_date: date, provider: str, cost_usd: Decimal,
    ) -> Decimal:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor,
                    "AddLlmDailySpend",
                    {
                        "SpendDate": spend_date,
                        "Provider": provider,
                        "CostUsd": cost_usd,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    return Decimal("0")
                return Decimal(str(row.CostUsd))
        except Exception as error:
            raise map_database_error(error)

    def read_spend_for_range(
        self,
        from_date: date,
        to_date: date,
        provider: str,
    ) -> list[pyodbc.Row]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor,
                    "ReadLlmSpendForRange",
                    {
                        "FromDate": from_date,
                        "ToDate": to_date,
                        "Provider": provider,
                    },
                )
                return cursor.fetchall()
        except Exception as error:
            raise map_database_error(error)
