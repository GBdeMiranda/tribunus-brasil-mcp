"""Reference series used to express amounts in terms a worker recognizes."""

import json
from typing import Any
from .utils import fetch_cached_file

# Banco Central SGS series 1619: salário mínimo nominal, monthly. Public, no key.
MINIMUM_WAGE_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1619/dados?formato=json"


async def minimum_wage_by_month() -> dict[str, float]:
    """Map 'YYYY-MM' to the minimum wage in force that month (empty dict if unavailable)."""
    path = await fetch_cached_file(MINIMUM_WAGE_URL, "bcb/salario-minimo.json", 30 * 24 * 3600)
    if not path:
        return {}
    try:
        series: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    result = {}
    for point in series:
        day, month, year = point["data"].split("/")
        result[f"{year}-{month}"] = float(point["valor"])
    return result


def in_minimum_wages(items: list[dict[str, Any]], wages: dict[str, float]) -> float | None:
    """
    Sum of each amount divided by the minimum wage of its own month, so a year that spans
    a wage adjustment is converted exactly. None if any month is missing from the series.
    """
    total = 0.0
    for item in items:
        year, month = item.get("ano"), item.get("mes")
        wage = wages.get(f"{year}-{int(month):02d}") if year and month else None
        if not wage:
            return None
        total += item["valor"] / wage
    return round(total, 1)
