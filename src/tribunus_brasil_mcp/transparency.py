import asyncio
import datetime
import json
from pathlib import Path
from typing import Any
from .identity import HOUSE_LABELS, resolve_parliamentarian
from .utils import (
    cache_max_age,
    cached_file_age_hours,
    fetch_all_pages,
    fetch_cached_file,
    iter_csv_rows,
)

CAMARA_BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
CAMARA_CEAP_BULK_URL = "https://www.camara.leg.br/cotas/Ano-{year}.csv.zip"
SENADO_ADM_BASE_URL = "https://adm.senado.gov.br/adm-dadosabertos/api/v1"
SENADO_CEAPS_URL = SENADO_ADM_BASE_URL + "/senadores/despesas_ceaps/{year}"

# Parsed per-deputy rows keyed by (year, deputy id, file mtime), so repeated calls within
# one evaluation don't re-scan the ~70 MB yearly CSV.
_ceap_rows_cache: dict[tuple[int, int, float], list[dict[str, str]]] = {}


def _normalize_float(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    try:
        clean = str(val).replace(".", "").replace(",", ".") if "," in str(val) else str(val)
        return float(clean)
    except (ValueError, TypeError):
        return 0.0


def _summarize_expenses(items: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    """Aggregate normalized expense items into totals, categories and top suppliers."""
    total = 0.0
    categories: dict[str, float] = {}
    suppliers: dict[str, dict[str, Any]] = {}
    without_receipt = {"quantidade": 0, "valor": 0.0}

    for item in items:
        val = item["valor"]
        total += val
        categories[item["tipo"]] = categories.get(item["tipo"], 0.0) + val

        # Group by tax id when present: the same company appears under spelling variants.
        key = item["cnpj_cpf"] or item["fornecedor"]
        if key not in suppliers:
            suppliers[key] = {"fornecedor": item["fornecedor"], "cnpj_cpf": item["cnpj_cpf"], "total": 0.0}
        suppliers[key]["total"] += val

        if not item.get("documento_url"):
            without_receipt["quantidade"] += 1
            without_receipt["valor"] += val

    top_suppliers = sorted(suppliers.values(), key=lambda x: x["total"], reverse=True)[:5]
    for s in top_suppliers:
        s["total"] = round(s["total"], 2)

    itemized = sorted(items, key=lambda x: str(x.get("data") or ""), reverse=True)

    return {
        "total_despesas": round(total, 2),
        "gastos_por_categoria": {
            k: round(v, 2) for k, v in sorted(categories.items(), key=lambda x: x[1], reverse=True)
        },
        "principais_fornecedores": top_suppliers,
        "quantidade_despesas": len(items),
        "despesas_sem_comprovante_digital": {
            "quantidade": without_receipt["quantidade"],
            "valor": round(without_receipt["valor"], 2),
        },
        "despesas": itemized[:limit],
    }


def _scan_ceap_file(path: Path, dep_id: int) -> list[dict[str, str]]:
    target = str(dep_id)
    return [row for row in iter_csv_rows(path) if row.get("ideCadastro") == target]


async def _load_deputy_ceap_bulk(dep_id: int, year: int) -> tuple[list[dict[str, str]], dict[str, Any]] | None:
    url = CAMARA_CEAP_BULK_URL.format(year=year)
    path = await fetch_cached_file(url, f"camara/ceap-{year}.csv.zip", cache_max_age(year))
    if not path:
        return None

    key = (year, dep_id, path.stat().st_mtime)
    if key not in _ceap_rows_cache:
        _ceap_rows_cache[key] = await asyncio.to_thread(_scan_ceap_file, path, dep_id)

    return _ceap_rows_cache[key], {"fonte": url, "idade_arquivo_horas": cached_file_age_hours(path)}


def _deputy_item_from_bulk(row: dict[str, str]) -> dict[str, Any]:
    return {
        "ano": int(row["numAno"]) if row.get("numAno") else None,
        "mes": int(row["numMes"]) if row.get("numMes") else None,
        "data": (row.get("datEmissao") or "")[:10] or None,
        "tipo": row.get("txtDescricao") or "Outros",
        "fornecedor": row.get("txtFornecedor") or "Não Informado",
        "cnpj_cpf": row.get("txtCNPJCPF") or "",
        "valor": round(_normalize_float(row.get("vlrLiquido")), 2),
        "documento_numero": row.get("txtNumero"),
        "documento_url": row.get("urlDocumento") or None,
    }


def _deputy_item_from_api(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ano": item.get("ano"),
        "mes": item.get("mes"),
        "data": item.get("dataDocumento"),
        "tipo": item.get("tipoDespesa") or "Outros",
        "fornecedor": item.get("nomeFornecedor") or "Não Informado",
        "cnpj_cpf": item.get("cnpjCpfFornecedor") or "",
        "valor": round(_normalize_float(item.get("valorLiquido") or item.get("valorDocumento")), 2),
        "documento_numero": item.get("numDocumento"),
        "documento_url": item.get("urlDocumento") or None,
    }


async def _fetch_deputy_year(dep_id: int, year: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Complete CEAP records for one year: yearly bulk file first, paginated API as fallback."""
    bulk = await _load_deputy_ceap_bulk(dep_id, year)
    if bulk is not None:
        rows, meta = bulk
        return [_deputy_item_from_bulk(r) for r in rows], {**meta, "completo": True}

    url = f"{CAMARA_BASE_URL}/deputados/{dep_id}/despesas"
    records, complete = await fetch_all_pages(url, params={"ano": year, "ordem": "DESC", "ordenarPor": "dataDocumento"})
    return [_deputy_item_from_api(r) for r in records], {"fonte": url, "completo": complete}


async def _get_deputy_expenses(
    deputy: dict[str, Any],
    year: int | None = None,
    month: int | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    dep_id = deputy["id"]
    target_year = year or datetime.date.today().year

    items, meta = await _fetch_deputy_year(dep_id, target_year)
    # Early in the year the current file may still be empty; fall back only when the
    # caller didn't ask for a specific year.
    if not items and year is None:
        target_year -= 1
        items, meta = await _fetch_deputy_year(dep_id, target_year)

    if month:
        items = [i for i in items if i["mes"] == int(month)]

    limitacoes = [
        "Valores líquidos (vlrLiquido), já descontadas glosas; restituições posteriores podem alterar o total.",
    ]
    if not meta.get("completo"):
        limitacoes.append("A fonte respondeu de forma incompleta: o total pode estar SUBESTIMADO.")

    return {
        "parlamentar": deputy["nome"],
        "id": dep_id,
        "casa": HOUSE_LABELS["camara"],
        "ano_referencia": target_year,
        "mes_referencia": month,
        **_summarize_expenses(items, limit),
        "metadados": {
            **meta,
            "periodo": f"{target_year}-{int(month):02d}" if month else str(target_year),
            "n_registros": len(items),
            "limitacoes": limitacoes,
        },
    }


def _load_json_file(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def _fetch_senator_year(code: int, year: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = SENADO_CEAPS_URL.format(year=year)
    path = await fetch_cached_file(url, f"senado/ceaps-{year}.json", cache_max_age(year))
    if not path:
        return [], {"fonte": url, "completo": False}

    try:
        records = await asyncio.to_thread(_load_json_file, path)
    except ValueError:
        return [], {"fonte": url, "completo": False}
    if not isinstance(records, list):
        records = []

    items = [
        {
            "ano": r.get("ano"),
            "mes": r.get("mes"),
            "data": r.get("data"),
            "tipo": r.get("tipoDespesa") or "Outros",
            "fornecedor": r.get("fornecedor") or "Não Informado",
            "cnpj_cpf": r.get("cpfCnpj") or "",
            "valor": round(_normalize_float(r.get("valorReembolsado")), 2),
            "documento_numero": r.get("documento"),
            # The CEAPS API publishes the receipt number only, never a link to the scan.
            "documento_url": None,
            "detalhamento": r.get("detalhamento"),
        }
        # Matching by the official code, never by name: name substrings collide
        # ('Eduardo' matches both Eduardo Braga and Eduardo Gomes).
        for r in records
        if str(r.get("codSenador")) == str(code)
    ]
    return items, {"fonte": url, "completo": True, "idade_arquivo_horas": cached_file_age_hours(path)}


async def _get_senator_expenses(
    senator: dict[str, Any],
    year: int | None = None,
    month: int | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    code = senator["id"]
    target_year = year or datetime.date.today().year

    items, meta = await _fetch_senator_year(code, target_year)
    if not items and year is None:
        target_year -= 1
        items, meta = await _fetch_senator_year(code, target_year)

    if month:
        items = [i for i in items if i["mes"] and int(i["mes"]) == int(month)]

    summary = _summarize_expenses(items, limit)
    # Every Senate item lacks a receipt link by construction, so this figure measures the
    # source, not the senator; drop it to avoid a misleading comparison with deputies.
    summary.pop("despesas_sem_comprovante_digital")

    limitacoes = [
        "Valores reembolsados pelo Senado (valorReembolsado).",
        "A API de CEAPS do Senado não publica link para o comprovante digitalizado.",
    ]
    if not meta.get("completo"):
        limitacoes.append("A fonte não respondeu: o total pode estar SUBESTIMADO.")

    return {
        "parlamentar": senator["nome"],
        "id": code,
        "casa": HOUSE_LABELS["senado"],
        "ano_referencia": target_year,
        "mes_referencia": month,
        **summary,
        "metadados": {
            **meta,
            "periodo": f"{target_year}-{int(month):02d}" if month else str(target_year),
            "n_registros": len(items),
            "limitacoes": limitacoes,
        },
    }


async def get_parliamentarian_expenses(
    name: str | None = None,
    house: str = "auto",
    year: int | None = None,
    month: int | None = None,
    limit: int = 15,
    parliamentarian_id: int | None = None,
) -> dict[str, Any]:
    """
    Retrieve itemized and aggregated expenses (CEAP) for a Brazilian parliamentarian.
    """
    parl = await resolve_parliamentarian(name, house=house, parliamentarian_id=parliamentarian_id)
    if "error" in parl:
        return parl

    if parl["casa"] == "senado":
        return await _get_senator_expenses(parl, year=year, month=month, limit=limit)
    return await _get_deputy_expenses(parl, year=year, month=month, limit=limit)
