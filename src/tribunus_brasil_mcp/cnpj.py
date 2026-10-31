"""
Supplier verification against the Receita Federal registry, with no API key.

BrasilAPI and minhareceita.org mirror the public CNPJ dataset with the same response
schema, so the second is a drop-in fallback. Answers are cached on disk per CNPJ.
"""

# TODO: Validate the query results

import asyncio
import json
import sys
import time
from typing import Any
import httpx
from .utils import CACHE_DIR, DEFAULT_HEADERS

PROVIDERS = (
    "https://brasilapi.com.br/api/cnpj/v1/{cnpj}",
    "https://minhareceita.org/{cnpj}",
)
CACHE_MAX_AGE = 30 * 24 * 3600
CONCURRENCY = 5

# Receita 'situação cadastral' codes.
SITUACOES = {1: "NULA", 2: "ATIVA", 3: "SUSPENSA", 4: "INAPTA", 8: "BAIXADA"}

NOT_FOUND = {"nao_localizado": True}


def only_digits(doc: str) -> str:
    return "".join(c for c in (doc or "") if c.isdigit())


def _cnpj_check_digits_ok(cnpj: str) -> bool:
    if len(cnpj) != 14 or len(set(cnpj)) == 1:
        return False

    def digit(base: str, weights: list[int]) -> str:
        rest = sum(int(d) * w for d, w in zip(base, weights)) % 11
        return "0" if rest < 2 else str(11 - rest)

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = digit(cnpj[:12], w1)
    d2 = digit(cnpj[:12] + d1, [6] + w1)
    return cnpj[12:] == d1 + d2


def classify_document(doc: str) -> tuple[str, str]:
    """
    Classify a supplier tax id as ('cnpj'|'cpf'|'nao_informado'|'invalido', normalized digits).

    'nao_informado' covers empty fields and the all-zero placeholders the Chamber uses
    (e.g. '000.000.000/0001-0'), which include foreign suppliers that have no CNPJ.
    Some CEAP rows drop the CNPJ's leading zero (13 digits); those are recovered only when
    the zero-padded number passes the check-digit test.
    """
    digits = only_digits(doc)
    if len(digits.strip("0")) <= 2:
        return "nao_informado", ""
    if len(digits) == 13 and _cnpj_check_digits_ok(digits.zfill(14)):
        digits = digits.zfill(14)
    if len(digits) == 14:
        return ("cnpj", digits) if _cnpj_check_digits_ok(digits) else ("invalido", digits)
    if len(digits) == 11:
        return "cpf", digits
    return "invalido", digits


def _summarize(data: dict[str, Any]) -> dict[str, Any]:
    code = data.get("situacao_cadastral")
    return {
        "razao_social": data.get("razao_social"),
        "situacao": data.get("descricao_situacao_cadastral") or SITUACOES.get(code),
        "data_situacao": data.get("data_situacao_cadastral"),
        "motivo_situacao": data.get("descricao_motivo_situacao_cadastral"),
        "data_abertura": data.get("data_inicio_atividade"),
        "cnae_principal": data.get("cnae_fiscal_descricao"),
        "municipio": data.get("municipio"),
        "uf": data.get("uf"),
    }


async def _fetch(client: httpx.AsyncClient, cnpj: str) -> dict[str, Any] | None:
    """Registry summary, NOT_FOUND when every provider says it doesn't exist, None on failure."""
    not_found_votes = 0
    for template in PROVIDERS:
        try:
            response = await client.get(
                template.format(cnpj=cnpj), headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
                timeout=30.0, follow_redirects=True,
            )
        except httpx.HTTPError as e:
            print(f"CNPJ lookup failed for {cnpj} at {template}: {e}", file=sys.stderr)
            continue
        if response.status_code == 200:
            return _summarize(response.json())
        if response.status_code == 404:
            not_found_votes += 1
    # Only claim "not found" when all providers agree: a single 404 may be a mirror lag.
    return NOT_FOUND if not_found_votes == len(PROVIDERS) else None


async def lookup_cnpjs(cnpjs: list[str]) -> dict[str, dict[str, Any] | None]:
    """Look up many CNPJs (14 digits). Value is None when the registry could not be reached."""
    cache_dir = CACHE_DIR / "cnpj"
    cache_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any] | None] = {}
    pending: list[str] = []

    for cnpj in dict.fromkeys(cnpjs):
        path = cache_dir / f"{cnpj}.json"
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE:
            results[cnpj] = json.loads(path.read_text(encoding="utf-8"))
        else:
            pending.append(cnpj)

    if pending:
        semaphore = asyncio.Semaphore(CONCURRENCY)
        async with httpx.AsyncClient() as client:
            async def one(cnpj: str) -> None:
                async with semaphore:
                    data = await _fetch(client, cnpj)
                results[cnpj] = data
                if data is not None:
                    (cache_dir / f"{cnpj}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            await asyncio.gather(*(one(c) for c in pending))

    return results
