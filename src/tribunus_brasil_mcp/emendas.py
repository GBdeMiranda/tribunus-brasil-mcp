"""
Parliamentary budget amendments (emendas) from the Portal da Transparência bulk download.

The Portal's REST API requires a registered key; its bulk download does not. Using the
bulk file keeps the MCP fully functional with zero configuration.
"""

import asyncio
from pathlib import Path
from typing import Any
from .utils import (
    cached_file_age_hours,
    fetch_cached_file,
    iter_csv_rows,
    normalize_name,
    parse_brl_number,
)

EMENDAS_BULK_URL = "https://portaldatransparencia.gov.br/download-de-dados/emendas-parlamentares/UNICO"
EMENDAS_MAX_AGE = 24 * 3600
ENCODING = "latin-1"

TIPO_TRANSFERENCIA_ESPECIAL = "Emenda Individual - Transferências Especiais"

# Recipient classification, from the Portal's 'Tipo Favorecido' / 'Natureza Jurídica'.
UNIDENTIFIED_RECIPIENT_TYPES = {"Sem informação", "Inválido", "Inscrição Genérica", "Inscrição Genéric"}
PUBLIC_NATURE_MARKERS = (
    "município", "estado ou distrito federal", "fundo público", "empresa pública",
    "sociedade de economia mista", "autarquia", "fundação pública", "órgão público",
    "consórcio público", "poder executivo", "poder legislativo", "poder judiciário",
)

_author_cache: dict[tuple[float, str, frozenset[str]], dict[str, Any]] = {}
_house_reference_cache: dict[tuple[float, frozenset[str]], dict[int, dict[str, Any]]] = {}
_recipient_cache: dict[tuple[float, tuple[str, ...]], list[dict[str, str]]] = {}


RECIPIENT_GROUPS = (
    "ente_ou_entidade_publica", "empresa_privada", "associacao_ou_fundacao_privada", "pessoa_fisica",
    "agente_financeiro", "execucao_federal_direta", "nao_identificado",
)
PRIVATE_GROUPS = ("empresa_privada", "associacao_ou_fundacao_privada")
NONPROFIT_NATURE_MARKERS = (
    "associação", "fundação privada", "organização religiosa", "organização social",
    "serviço social autônomo", "entidade sindical", "cooperativa", "partido político",
)

# Banks that receive transfers as paying agents; their seat says nothing about where the money goes.
FINANCIAL_AGENTS = ("BANCO DO BRASIL", "CAIXA ECONOMICA FEDERAL")


def _recipient_group(tipo: str, natureza: str, favorecido: str = "") -> str:
    if tipo in UNIDENTIFIED_RECIPIENT_TYPES:
        return "nao_identificado"
    if favorecido.upper().startswith(FINANCIAL_AGENTS):
        return "agente_financeiro"
    if tipo == "Unidade Gestora":
        return "execucao_federal_direta"
    if tipo.lower().startswith("pessoa f"):
        return "pessoa_fisica"
    if any(marker in natureza.lower() for marker in PUBLIC_NATURE_MARKERS):
        return "ente_ou_entidade_publica"
    if any(marker in natureza.lower() for marker in NONPROFIT_NATURE_MARKERS):
        return "associacao_ou_fundacao_privada"
    return "empresa_privada"


def _scan_author_amendments(path: Path, names: set[str], years: frozenset[str]) -> dict[str, Any]:
    """Amendments of `years` whose author name matches exactly (accent/case-insensitive)."""
    rows = []
    codes: dict[str, str] = {}
    for row in iter_csv_rows(path, member="EmendasParlamentares.csv", encoding=ENCODING):
        if row.get("Ano da Emenda") not in years:
            continue
        author = row.get("Nome do Autor da Emenda") or ""
        if normalize_name(author) in names:
            rows.append(row)
            codes[row.get("Código do Autor da Emenda") or ""] = author
    return {"linhas": rows, "codigos_autor": codes}


def _scan_recipients(path: Path, code_prefixes: tuple[str, ...]) -> list[dict[str, str]]:
    # Amendment codes are year + author code + sequence (e.g. 2025 2726 0001).
    return [
        row for row in iter_csv_rows(path, member="EmendasParlamentares_PorFavorecido.csv", encoding=ENCODING)
        if (row.get("Código da Emenda") or "").startswith(code_prefixes)
    ]


def _scan_house_reference(path: Path, years: frozenset[str]) -> dict[int, dict[str, Any]]:
    """Per-year reference over all authors of individual amendments (deputies and senators together)."""
    per_author: dict[tuple[str, str], list[float]] = {}  # (year, code) -> [individual, transf. especial]
    for row in iter_csv_rows(path, member="EmendasParlamentares.csv", encoding=ENCODING):
        code = row.get("Código do Autor da Emenda") or ""
        tipo = row.get("Tipo de Emenda") or ""
        year = row.get("Ano da Emenda") or ""
        if year not in years or code in ("", "S/I") or not tipo.startswith("Emenda Individual"):
            continue
        totals = per_author.setdefault((year, code), [0.0, 0.0])
        value = parse_brl_number(row.get("Valor Empenhado"))
        totals[0] += value
        if tipo == TIPO_TRANSFERENCIA_ESPECIAL:
            totals[1] += value

    reference = {}
    for year in years:
        authors = [t for (y, _), t in per_author.items() if y == year]
        reference[int(year)] = {
            "autores_com_transferencia_especial": sum(1 for _, te in authors if te > 0),
            "n_autores": len(authors),
        }
    return reference


async def get_amendments(names: list[str | None], years: list[int]) -> dict[str, Any]:
    """
    Individual amendments authored in `years` and who received the money.

    `names` are the parliamentary and civil names; the Portal identifies authors only by
    name, so a miss is reported as 'not located', never as 'no amendments'.
    """
    path = await fetch_cached_file(EMENDAS_BULK_URL, "portal/emendas-parlamentares.zip", EMENDAS_MAX_AGE)
    meta = {"fonte": EMENDAS_BULK_URL}
    if not path:
        return {"erro": "Arquivo de emendas do Portal da Transparência indisponível.", "metadados": meta}
    meta["idade_arquivo_horas"] = cached_file_age_hours(path)
    mtime = path.stat().st_mtime
    year_keys = frozenset(str(y) for y in years)

    normalized = {normalize_name(n) for n in names if n}
    key = (mtime, "|".join(sorted(normalized)), year_keys)
    if key not in _author_cache:
        _author_cache[key] = await asyncio.to_thread(_scan_author_amendments, path, normalized, year_keys)
    found = _author_cache[key]

    codes = {c: n for c, n in found["codigos_autor"].items() if c and c != "S/I"}
    if not found["linhas"]:
        return {"localizado": False, "metadados": meta}
    if len(codes) > 1:
        # Two author codes under the same name: homonyms we cannot separate.
        return {
            "localizado": False,
            "erro": "Mais de um autor com o mesmo nome no Portal; não é possível atribuir as emendas com segurança.",
            "codigos_autor": codes,
            "metadados": meta,
        }

    author_code = next(iter(codes), None)
    recipients: list[dict[str, str]] = []
    if author_code:
        prefixes = tuple(sorted(f"{y}{author_code}" for y in year_keys))
        rkey = (mtime, prefixes)
        if rkey not in _recipient_cache:
            _recipient_cache[rkey] = await asyncio.to_thread(_scan_recipients, path, prefixes)
        recipients = _recipient_cache[rkey]

    hkey = (mtime, year_keys)
    if hkey not in _house_reference_cache:
        _house_reference_cache[hkey] = await asyncio.to_thread(_scan_house_reference, path, year_keys)

    amendments = [
        {
            "ano": int(r.get("Ano da Emenda")),
            "codigo": r.get("Código da Emenda"),
            "numero": r.get("Número da emenda"),
            "tipo": r.get("Tipo de Emenda"),
            "localidade": r.get("Localidade de aplicação do recurso"),
            "funcao": r.get("Nome Função"),
            "acao": r.get("Nome Ação"),
            "empenhado": parse_brl_number(r.get("Valor Empenhado")),
            "pago": parse_brl_number(r.get("Valor Pago")) + parse_brl_number(r.get("Valor Restos A Pagar Pagos")),
        }
        for r in found["linhas"]
    ]
    payments = [
        {
            "ano_emenda": int((r.get("Código da Emenda") or "0")[:4] or 0),
            "codigo_emenda": r.get("Código da Emenda"),
            "tipo_emenda": r.get("Tipo de Emenda"),
            "favorecido": r.get("Favorecido"),
            "documento_favorecido": r.get("Código do Favorecido"),
            "natureza_juridica": r.get("Natureza Jurídica"),
            "grupo": _recipient_group(
                r.get("Tipo Favorecido") or "", r.get("Natureza Jurídica") or "", r.get("Favorecido") or ""
            ),
            "municipio": r.get("Município Favorecido"),
            "uf": r.get("UF Favorecido"),
            "valor": parse_brl_number(r.get("Valor Recebido")),
        }
        for r in recipients
    ]
    return {
        "localizado": True,
        "nome_no_portal": codes.get(author_code) if author_code else None,
        "codigo_autor": author_code,
        "emendas": amendments,
        "pagamentos": payments,
        "referencia_autores": _house_reference_cache[hkey],  # by year
        "metadados": meta,
    }
