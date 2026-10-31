"""
Unambiguous identification of parliamentarians.

Every audit starts here: evaluating the wrong person (a homonym, or a substring match
such as 'Lira' -> 'Átila Lira') invalidates everything downstream, so ambiguous names
are reported back with the candidate list instead of silently picking the first hit.
"""

import asyncio
import urllib.parse
from typing import Any
from .utils import make_request, normalize_name

SENADO_BASE_URL = "https://legis.senado.leg.br/dadosabertos"
CAMARA_BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"

HOUSE_LABELS = {"senado": "Senado Federal", "camara": "Câmara dos Deputados"}


def _senator_candidate(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "casa": "senado",
        "id": int(info.get("CodigoParlamentar")),
        "nome": info.get("NomeParlamentar"),
        "nome_civil": info.get("NomeCompletoParlamentar"),
        "partido": info.get("SiglaPartidoParlamentar"),
        "uf": info.get("UfParlamentar"),
        "raw": info,
    }


def _deputy_candidate(dep: dict[str, Any]) -> dict[str, Any]:
    return {
        "casa": "camara",
        "id": int(dep.get("id")),
        "nome": dep.get("nome"),
        "nome_civil": dep.get("nomeCivil"),
        "partido": dep.get("siglaPartido"),
        "uf": dep.get("siglaUf"),
        "raw": dep,
    }


async def _senator_candidates_by_name(name: str) -> list[dict[str, Any]]:
    query = normalize_name(name)
    data = await make_request(f"{SENADO_BASE_URL}/senador/lista/atual") or {}
    senators = data.get("ListaParlamentarEmExercicio", {}).get("Parlamentares", {}).get("Parlamentar", [])
    if isinstance(senators, dict):
        senators = [senators]

    candidates = []
    for s in senators:
        info = s.get("IdentificacaoParlamentar", {})
        names = (normalize_name(info.get("NomeParlamentar", "")), normalize_name(info.get("NomeCompletoParlamentar", "")))
        if any(query in n for n in names):
            candidates.append(_senator_candidate(info))
    return candidates


async def _deputy_candidates_by_name(name: str) -> list[dict[str, Any]]:
    encoded_name = urllib.parse.quote(name)
    data = await make_request(f"{CAMARA_BASE_URL}/deputados?nome={encoded_name}") or {}
    return [_deputy_candidate(d) for d in data.get("dados", []) if d.get("id")]


async def _senator_by_id(code: int) -> dict[str, Any] | None:
    data = await make_request(f"{SENADO_BASE_URL}/senador/{code}") or {}
    info = data.get("DetalheParlamentar", {}).get("Parlamentar", {}).get("IdentificacaoParlamentar")
    return _senator_candidate(info) if info else None


async def _deputy_by_id(dep_id: int) -> dict[str, Any] | None:
    data = await make_request(f"{CAMARA_BASE_URL}/deputados/{dep_id}") or {}
    d = data.get("dados")
    if not d:
        return None
    ultimo = d.get("ultimoStatus", {})
    return _deputy_candidate({
        "id": d.get("id") or dep_id,
        "nome": ultimo.get("nome") or ultimo.get("nomeEleitoral"),
        "nomeCivil": d.get("nomeCivil"),
        "siglaPartido": ultimo.get("siglaPartido"),
        "siglaUf": ultimo.get("siglaUf"),
        "email": ultimo.get("email"),
        "urlFoto": ultimo.get("urlFoto"),
    })


def _pick(name: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the single intended match, or None when the name is ambiguous."""
    if len(candidates) == 1:
        return candidates[0]
    query = normalize_name(name)
    exact = [
        c for c in candidates
        if query in (normalize_name(c.get("nome") or ""), normalize_name(c.get("nome_civil") or ""))
    ]
    return exact[0] if len(exact) == 1 else None


def _public(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate["id"],
        "casa": HOUSE_LABELS[candidate["casa"]],
        "nome": candidate["nome"],
        "partido": candidate["partido"],
        "uf": candidate["uf"],
    }


async def resolve_parliamentarian(
    name: str | None = None,
    house: str = "auto",
    parliamentarian_id: int | None = None,
) -> dict[str, Any]:
    """
    Resolve a name or official ID to exactly one parliamentarian.

    Returns the candidate dict (keys: casa, id, nome, nome_civil, partido, uf, raw) or a dict
    with an 'error' key. Ambiguous lookups include 'candidatos' so the caller can retry
    with `parliamentarianId`.
    """
    target_house = (house or "auto").lower()
    houses = [h for h in ("senado", "camara") if target_house in ("auto", h)]
    if not houses:
        return {"error": f"Invalid house '{house}'. Use 'auto', 'senado' or 'camara'."}

    if parliamentarian_id is not None:
        lookups = {"senado": _senator_by_id, "camara": _deputy_by_id}
        found = await asyncio.gather(*(lookups[h](int(parliamentarian_id)) for h in houses))
        candidates = [c for c in found if c]
        if not candidates:
            return {"error": f"No parliamentarian with id {parliamentarian_id} in {house}."}
        if len(candidates) > 1:
            return {
                "error": f"Id {parliamentarian_id} exists in both houses; pass house='senado' or 'camara'.",
                "candidatos": [_public(c) for c in candidates],
            }
        return candidates[0]

    if not name or not name.strip():
        return {"error": "Provide a name or parliamentarianId."}

    lookups = {"senado": _senator_candidates_by_name, "camara": _deputy_candidates_by_name}
    results = await asyncio.gather(*(lookups[h](name) for h in houses))
    candidates = [c for group in results for c in group]

    if not candidates:
        return {"error": f"Parliamentarian '{name}' not found in {house}."}

    chosen = _pick(name, candidates)
    if chosen:
        return chosen

    return {
        "error": (
            f"Name '{name}' is ambiguous ({len(candidates)} matches). "
            "Retry with the full parliamentary name or with parliamentarianId."
        ),
        "candidatos": [_public(c) for c in candidates],
    }
