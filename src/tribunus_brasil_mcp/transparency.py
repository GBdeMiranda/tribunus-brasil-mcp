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
    normalize_name,
    parse_brl_number,
)

CAMARA_BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
CAMARA_CEAP_BULK_URL = "https://www.camara.leg.br/cotas/Ano-{year}.csv.zip"
SENADO_ADM_BASE_URL = "https://adm.senado.gov.br/adm-dadosabertos/api/v1"
SENADO_CEAPS_URL = SENADO_ADM_BASE_URL + "/senadores/despesas_ceaps/{year}"

# A category counts as "receipt never published by the institution" when at least this
# share of the whole house's receipts in it lack a document link. In 2025 telephony,
# SIGEPA/RPA air tickets and housing-aid top-ups were 100% without a link for every deputy:
# billed centrally by the Chamber, so their absence says nothing about any single deputy.
INSTITUTIONAL_NO_RECEIPT_SHARE = 0.99

# Parsed per-deputy rows keyed by (year, deputy id, file mtime), so repeated calls within
# one evaluation don't re-scan the ~70 MB yearly CSV.
_ceap_rows_cache: dict[tuple[int, int, float], list[dict[str, str]]] = {}
_camara_house_stats_cache: dict[tuple[int, float], dict[str, Any]] = {}
_senado_house_stats_cache: dict[tuple[int, float], dict[str, Any]] = {}

_normalize_float = parse_brl_number


def is_self_promotion_category(category: str) -> bool:
    """CEAP 'Divulgação da atividade parlamentar' (both houses, spelling varies)."""
    return normalize_name(category).startswith("divulgacao da atividade parlamentar")


def _summarize_expenses(
    items: list[dict[str, Any]],
    limit: int,
    institutional_categories: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
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

        if not item.get("documento_url") and item["tipo"] not in institutional_categories:
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


def _scan_camara_house(path: Path) -> dict[str, Any]:
    """One pass over the yearly CEAP file: per-deputy, per-category counts and values."""
    per_dep: dict[str, dict[str, Any]] = {}
    cat_totals: dict[str, list[int]] = {}
    for row in iter_csv_rows(path):
        dep_id = row.get("ideCadastro")
        if not dep_id:  # party leaderships and other non-deputy cost centers
            continue
        cat = row.get("txtDescricao") or "Outros"
        val = parse_brl_number(row.get("vlrLiquido"))
        missing = not row.get("urlDocumento")

        dep = per_dep.setdefault(dep_id, {
            "nome": row.get("txNomeParlamentar"), "partido": row.get("sgPartido"),
            "uf": row.get("sgUF"), "categorias": {},
        })
        c = dep["categorias"].setdefault(cat, [0, 0.0, 0, 0.0])  # n, valor, n_sem_url, valor_sem_url
        c[0] += 1
        c[1] += val
        if missing:
            c[2] += 1
            c[3] += val

        t = cat_totals.setdefault(cat, [0, 0])
        t[0] += 1
        t[1] += missing

    institutional = {
        cat for cat, (n, n_missing) in cat_totals.items()
        if n and n_missing / n >= INSTITUTIONAL_NO_RECEIPT_SHARE
    }

    parlamentares = {}
    for dep_id, dep in per_dep.items():
        cats = dep.pop("categorias")
        parlamentares[int(dep_id)] = {
            **dep,
            "n": sum(c[0] for c in cats.values()),
            "total": sum(c[1] for c in cats.values()),
            "divulgacao_n": sum(c[0] for k, c in cats.items() if is_self_promotion_category(k)),
            "divulgacao_valor": sum(c[1] for k, c in cats.items() if is_self_promotion_category(k)),
            "n_comprovavel": sum(c[0] for k, c in cats.items() if k not in institutional),
            "valor_comprovavel": sum(c[1] for k, c in cats.items() if k not in institutional),
            "sem_comprovante_n": sum(c[2] for k, c in cats.items() if k not in institutional),
            "sem_comprovante_valor": sum(c[3] for k, c in cats.items() if k not in institutional),
        }
    return {"parlamentares": parlamentares, "categorias_institucionais": sorted(institutional)}


async def camara_ceap_house_stats(year: int) -> dict[str, Any] | None:
    """
    House-wide CEAP aggregates for one year: per-deputy totals (the peer reference) and the
    categories whose receipts the Chamber itself never publishes.
    """
    url = CAMARA_CEAP_BULK_URL.format(year=year)
    path = await fetch_cached_file(url, f"camara/ceap-{year}.csv.zip", cache_max_age(year))
    if not path:
        return None
    key = (year, path.stat().st_mtime)
    if key not in _camara_house_stats_cache:
        _camara_house_stats_cache[key] = await asyncio.to_thread(_scan_camara_house, path)
    return {**_camara_house_stats_cache[key], "fonte": url, "idade_arquivo_horas": cached_file_age_hours(path)}


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

    house_stats = await camara_ceap_house_stats(target_year)
    institutional = set(house_stats["categorias_institucionais"]) if house_stats else set()

    limitacoes = [
        "Valores líquidos (vlrLiquido), já descontadas glosas; restituições posteriores podem alterar o total.",
    ]
    if institutional:
        limitacoes.append(
            "'despesas_sem_comprovante_digital' ignora categorias faturadas pela própria Câmara, "
            f"sem comprovante para nenhum deputado: {', '.join(sorted(institutional))}."
        )
    else:
        limitacoes.append(
            "Não foi possível identificar as categorias sem comprovante por padrão institucional; "
            "'despesas_sem_comprovante_digital' pode incluí-las."
        )
    if not meta.get("completo"):
        limitacoes.append("A fonte respondeu de forma incompleta: o total pode estar SUBESTIMADO.")

    return {
        "parlamentar": deputy["nome"],
        "id": dep_id,
        "casa": HOUSE_LABELS["camara"],
        "ano_referencia": target_year,
        "mes_referencia": month,
        **_summarize_expenses(items, limit, institutional),
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


async def _load_senado_ceaps(year: int) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    url = SENADO_CEAPS_URL.format(year=year)
    path = await fetch_cached_file(url, f"senado/ceaps-{year}.json", cache_max_age(year))
    if not path:
        return None, {"fonte": url, "completo": False}
    try:
        records = await asyncio.to_thread(_load_json_file, path)
    except ValueError:
        return None, {"fonte": url, "completo": False}
    meta = {"fonte": url, "completo": True, "idade_arquivo_horas": cached_file_age_hours(path), "mtime": path.stat().st_mtime}
    return (records if isinstance(records, list) else []), meta


async def _fetch_senator_year(code: int, year: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, meta = await _load_senado_ceaps(year)
    meta.pop("mtime", None)
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
        for r in (records or [])
        if str(r.get("codSenador")) == str(code)
    ]
    return items, meta


async def senado_ceaps_house_stats(year: int) -> dict[str, Any] | None:
    """Per-senator CEAPS totals for one year (peer reference)."""
    records, meta = await _load_senado_ceaps(year)
    if records is None:
        return None
    key = (year, meta.pop("mtime"))
    if key not in _senado_house_stats_cache:
        per_sen: dict[int, dict[str, Any]] = {}
        for r in records:
            if r.get("codSenador") is None:
                continue
            sen = per_sen.setdefault(int(r["codSenador"]), {
                "nome": r.get("nomeSenador"), "n": 0, "total": 0.0, "divulgacao_n": 0, "divulgacao_valor": 0.0,
            })
            val = _normalize_float(r.get("valorReembolsado"))
            sen["n"] += 1
            sen["total"] += val
            if is_self_promotion_category(r.get("tipoDespesa") or ""):
                sen["divulgacao_n"] += 1
                sen["divulgacao_valor"] += val
        _senado_house_stats_cache[key] = {"parlamentares": per_sen}
    return {**_senado_house_stats_cache[key], "fonte": meta["fonte"], "idade_arquivo_horas": meta["idade_arquivo_horas"]}


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
