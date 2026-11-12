# Money: Decimal and two-shot rounding

Exact-decimal rule and the canonical labor rounding policy, with every call site.

> Extracted verbatim from `CLAUDE.md` on 2026-11-11 (U-788) to take it off the
> per-session load. **These are standing rules, not history** — they bind the same as
> before. Read this file when touching the area; CLAUDE.md keeps the one-line rule and
> links here.

---

- **Decimal precision**: All financial fields (rate, amount, markup, price, total_amount) must use `Decimal(str(value))` — never `float()`. Float round-trips corrupt values
- **Labor money is TWO-SHOT cent rounding**: Canonical policy is `cost = round(hours × rate, 2)` then `price = round(cost × (1 + markup), 2)`, half away from zero; a single-shot `hours * rate * (1 + markup)` rounded once can land a cent off. T-SQL `ROUND(x, 2)` and the web's `shared/money` `roundMoney` were proven identical over 100,000 exhaustive rounding decisions and 41,882 end-to-end fixtures. Canonical implementations: `shared/api/money.py` (`round_money` + `labor_price_two_shot`; Python `ROUND_HALF_UP` is half-away-from-zero and matches T-SQL `ROUND` + the web `roundMoney`), `build.one.web/src/shared/money.ts` (`computeAmount` then `applyMarkup`), and `dbo.AggregateTimeEntryOnSubmit` in `entities/time_entry/sql/dbo.time_entry.sql`. **Third site closed (U-203):** `labor_price_two_shot` is called by `ContractLaborService.create` + `update_by_public_id` and by `ContractLabor.calculate_total_amount` / `ContractLaborLineItem.calculate_price`.
