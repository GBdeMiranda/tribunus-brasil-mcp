import datetime
import urllib.parse
from typing import Any
from .utils import make_request

SENADO_BASE_URL = "https://legis.senado.leg.br/dadosabertos"
CAMARA_BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
SENADO_ADM_BASE_URL = "https://adm.senado.gov.br/adm-dadosabertos/api/v1"


async def _find_senator_info(name: str) -> dict[str, Any] | None:
    search_name = name.lower()
    data = await make_request(f"{SENADO_BASE_URL}/senador/lista/atual") or {}
    senators = data.get("ListaParlamentarEmExercicio", {}).get("Parlamentares", {}).get("Parlamentar", [])
    if isinstance(senators, dict):
        senators = [senators]

    for s in senators:
        info = s.get("IdentificacaoParlamentar", {})
        full = info.get("NomeCompletoParlamentar", "").lower()
        parl = info.get("NomeParlamentar", "").lower()
        if search_name in full or search_name in parl:
            return info
    return None


async def _find_deputy_info(name: str) -> dict[str, Any] | None:
    encoded_name = urllib.parse.quote(name)
    data = await make_request(f"{CAMARA_BASE_URL}/deputados?nome={encoded_name}") or {}
    deputados = data.get("dados", [])
    if not deputados:
        return None
    return deputados[0]


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


async def _get_deputy_expenses(
    deputy: dict[str, Any],
    year: int | None = None,
    month: int | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    dep_id = deputy.get("id")
    target_year = year or datetime.date.today().year

    params: dict[str, Any] = {
        "ano": target_year,
        "itens": 100,
        "ordem": "DESC",
        "ordenarPor": "dataDocumento",
    }
    if month:
        params["mes"] = month

    url = f"{CAMARA_BASE_URL}/deputados/{dep_id}/despesas"
    data = await make_request(url, params=params) or {}
    expenses_raw = data.get("dados", [])

    # If no data found for current year and year was not explicitly given, try previous year
    if not expenses_raw and year is None:
        target_year -= 1
        params["ano"] = target_year
        data = await make_request(url, params=params) or {}
        expenses_raw = data.get("dados", [])

    total = 0.0
    categories: dict[str, float] = {}
    suppliers: dict[str, dict[str, Any]] = {}
    itemized: list[dict[str, Any]] = []

    for item in expenses_raw:
        val = _normalize_float(item.get("valorLiquido") or item.get("valorDocumento"))
        cat = item.get("tipoDespesa") or "Outros"
        sup_name = item.get("nomeFornecedor") or "Não Informado"
        sup_doc = item.get("cnpjCpfFornecedor") or ""

        total += val
        categories[cat] = categories.get(cat, 0.0) + val

        if sup_name not in suppliers:
            suppliers[sup_name] = {"fornecedor": sup_name, "cnpj_cpf": sup_doc, "total": 0.0}
        suppliers[sup_name]["total"] += val

        itemized.append({
            "ano": item.get("ano"),
            "mes": item.get("mes"),
            "data": item.get("dataDocumento"),
            "tipo": cat,
            "fornecedor": sup_name,
            "cnpj_cpf": sup_doc,
            "valor": round(val, 2),
            "documento_numero": item.get("numDocumento"),
            "documento_url": item.get("urlDocumento"),
        })

    top_suppliers = sorted(suppliers.values(), key=lambda x: x["total"], reverse=True)[:5]
    for s in top_suppliers:
        s["total"] = round(s["total"], 2)

    categories_sorted = {
        k: round(v, 2)
        for k, v in sorted(categories.items(), key=lambda x: x[1], reverse=True)
    }

    return {
        "parlamentar": deputy.get("nome"),
        "casa": "Câmara dos Deputados",
        "ano_referencia": target_year,
        "mes_referencia": month,
        "total_despesas": round(total, 2),
        "gastos_por_categoria": categories_sorted,
        "principais_fornecedores": top_suppliers,
        "quantidade_despesas": len(expenses_raw),
        "despesas": itemized[:limit],
    }


async def _get_senator_expenses(
    senator: dict[str, Any],
    year: int | None = None,
    month: int | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    target_year = year or datetime.date.today().year
    sen_name = senator.get("NomeParlamentar", "").lower()
    full_name = senator.get("NomeCompletoParlamentar", "").lower()

    url = f"{SENADO_ADM_BASE_URL}/senadores/despesas_ceaps/{target_year}"
    raw_data = await make_request(url)

    # If no data for target_year and not explicitly set, try previous year
    if not raw_data and year is None:
        target_year -= 1
        url = f"{SENADO_ADM_BASE_URL}/senadores/despesas_ceaps/{target_year}"
        raw_data = await make_request(url)

    records: list[dict[str, Any]] = []
    if isinstance(raw_data, list):
        records = raw_data
    elif isinstance(raw_data, dict):
        records = (
            raw_data.get("despesas", [])
            or raw_data.get("dados", [])
            or raw_data.get("DespesasCeaps", [])
        )

    # Match senator by name or full name
    senator_expenses: list[dict[str, Any]] = []
    for r in records:
        r_sen = str(r.get("SENADOR") or r.get("senador") or r.get("nomeSenador") or "").lower()
        if sen_name in r_sen or (full_name and full_name in r_sen):
            senator_expenses.append(r)

    total = 0.0
    categories: dict[str, float] = {}
    suppliers: dict[str, dict[str, Any]] = {}
    itemized: list[dict[str, Any]] = []

    for item in senator_expenses:
        item_month = item.get("MES") or item.get("mes")
        if month and item_month and int(item_month) != int(month):
            continue

        val = _normalize_float(
            item.get("VALOR_REEMBOLSADO")
            or item.get("valorReembolsado")
            or item.get("VALOR")
            or item.get("valor")
        )
        cat = item.get("TIPO_DESPESA") or item.get("tipoDespesa") or "Outros"
        sup_name = item.get("FORNECEDOR") or item.get("fornecedor") or "Não Informado"
        sup_doc = item.get("CNPJ_CPF") or item.get("cnpjCpf") or ""
        doc_url = item.get("DOCUMENTO") or item.get("documento") or item.get("urlDocumento")

        total += val
        categories[cat] = categories.get(cat, 0.0) + val

        if sup_name not in suppliers:
            suppliers[sup_name] = {"fornecedor": sup_name, "cnpj_cpf": sup_doc, "total": 0.0}
        suppliers[sup_name]["total"] += val

        itemized.append({
            "ano": item.get("ANO") or item.get("ano") or target_year,
            "mes": item_month,
            "data": item.get("DATA") or item.get("data"),
            "tipo": cat,
            "fornecedor": sup_name,
            "cnpj_cpf": sup_doc,
            "valor": round(val, 2),
            "documento_numero": item.get("NUM_DOCUMENTO") or item.get("numDocumento"),
            "documento_url": doc_url,
        })

    top_suppliers = sorted(suppliers.values(), key=lambda x: x["total"], reverse=True)[:5]
    for s in top_suppliers:
        s["total"] = round(s["total"], 2)

    categories_sorted = {
        k: round(v, 2)
        for k, v in sorted(categories.items(), key=lambda x: x[1], reverse=True)
    }

    return {
        "parlamentar": senator.get("NomeParlamentar"),
        "casa": "Senado Federal",
        "ano_referencia": target_year,
        "mes_referencia": month,
        "total_despesas": round(total, 2),
        "gastos_por_categoria": categories_sorted,
        "principais_fornecedores": top_suppliers,
        "quantidade_despesas": len(senator_expenses),
        "despesas": itemized[:limit],
    }


async def get_parliamentarian_expenses(
    name: str,
    house: str = "auto",
    year: int | None = None,
    month: int | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    """
    Retrieve itemized and aggregated expenses (CEAP) for a Brazilian parliamentarian.
    """
    target_house = house.lower()

    if target_house in ("auto", "senado"):
        senator = await _find_senator_info(name)
        if senator:
            return await _get_senator_expenses(senator, year=year, month=month, limit=limit)

    if target_house in ("auto", "camara"):
        deputy = await _find_deputy_info(name)
        if deputy:
            return await _get_deputy_expenses(deputy, year=year, month=month, limit=limit)

    return {"error": f"Parliamentarian '{name}' not found in {house}."}
