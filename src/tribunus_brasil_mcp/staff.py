"""
Who works in each parliamentarian's office, from the houses' own staff registers (current snapshot).

Câmara: parliamentary secretaries and special-nature posts allocated to the deputy's office.
Senado: active staff of the senator's office in Brasília and of the support offices in the state.
"""

import asyncio
import collections
import json
import re
import statistics
from pathlib import Path
from typing import Any
from .utils import CURRENT_YEAR_MAX_AGE, cached_file_age_hours, fetch_cached_file, iter_csv_rows, normalize_name

CAMARA_STAFF_URL = "https://dadosabertos.camara.leg.br/arquivos/funcionarios/csv/funcionarios.csv"
SENADO_STAFF_URL = "https://adm.senado.gov.br/adm-dadosabertos/api/v1/servidores/servidores"
# 'Gabinete do Senador X', 'Escritório de Apoio nº 1 da Senadora Y'...
SENADO_OFFICE = re.compile(r"^(Gabinete|Escritório de Apoio)\b.*?\b(?:do|da) Senador(?:a)? (.+)$")

_staff_cache: dict[tuple[str, float], dict[str, Any]] = {}


def _scan_camara(path: Path) -> dict[str, list[dict[str, str]]]:
    offices: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for r in iter_csv_rows(path):
        uri = r.get("uriLotacao") or ""
        if "/deputados/" not in uri or r.get("grupo") == "Parlamentar":
            continue
        offices[uri.rsplit("/", 1)[-1]].append({
            "grupo": r.get("grupo") or "", "cargo": r.get("cargo") or "", "desde": r.get("dataInicioHistorico") or "",
        })
    return offices


def _scan_senado(path: Path) -> dict[str, dict[str, list[dict[str, str]]]]:
    offices: dict[str, dict[str, list[dict[str, str]]]] = collections.defaultdict(lambda: {"gabinete": [], "escritorios": []})
    for r in json.loads(path.read_text(encoding="utf-8")):
        if r.get("situacao") != "ATIVO" or r.get("vinculo") == "PARLAMENTAR":
            continue
        match = SENADO_OFFICE.match(((r.get("lotacao") or {}).get("nome") or "").strip())
        if not match:
            continue
        place = "gabinete" if match.group(1) == "Gabinete" else "escritorios"
        offices[normalize_name(match.group(2))][place].append({"vinculo": r.get("vinculo") or "", "desde": str(r.get("ano_admissao") or "")})
    return offices


async def _load(house: str) -> tuple[dict[str, Any], Path] | None:
    if house == "camara":
        path = await fetch_cached_file(CAMARA_STAFF_URL, "camara/funcionarios.csv", CURRENT_YEAR_MAX_AGE)
        scan = _scan_camara
    else:
        path = await fetch_cached_file(SENADO_STAFF_URL, "senado/servidores.json", CURRENT_YEAR_MAX_AGE,
                                       headers={"Accept": "application/json"})
        scan = _scan_senado
    if not path:
        return None
    key = (house, path.stat().st_mtime)
    if key not in _staff_cache:
        _staff_cache[key] = await asyncio.to_thread(scan, path)
    return _staff_cache[key], path


def _house_reference(sizes: list[int]) -> dict[str, Any] | None:
    if not sizes:
        return None
    return {"mediana": statistics.median(sizes), "minimo": min(sizes), "maximo": max(sizes), "gabinetes": len(sizes)}


async def office_staff(parl: dict[str, Any]) -> dict[str, Any]:
    """Current number of people in the office, by kind of post, with the house's spread as reference."""
    loaded = await _load(parl["casa"])
    if not loaded:
        return {"erro": "Cadastro de pessoal da Casa indisponível."}
    offices, path = loaded
    meta = {
        "fonte": CAMARA_STAFF_URL if parl["casa"] == "camara" else SENADO_STAFF_URL,
        "idade_arquivo_horas": cached_file_age_hours(path),
        "limitacoes": ["Retrato do quadro atual, sem histórico de contratações e desligamentos."],
    }

    if parl["casa"] == "camara":
        people = offices.get(str(parl["id"]), [])
        return {
            "quantidade": len(people),
            "por_grupo": dict(collections.Counter(p["grupo"] for p in people)),
            "por_cargo": dict(collections.Counter(p["cargo"] for p in people).most_common()),
            "referencia_casa": _house_reference([len(v) for v in offices.values()]),
            "metadados": meta,
        }

    office = offices.get(normalize_name(parl["nome"]))
    if not office:
        return {"quantidade": None, "erro": "Gabinete não localizado pelo nome parlamentar no cadastro do Senado.", "metadados": meta}
    return {
        "quantidade": len(office["gabinete"]) + len(office["escritorios"]),
        "gabinete_brasilia": len(office["gabinete"]),
        "escritorios_no_estado": len(office["escritorios"]),
        "por_vinculo": dict(collections.Counter(p["vinculo"] for p in office["gabinete"] + office["escritorios"])),
        "referencia_casa": _house_reference([len(o["gabinete"]) + len(o["escritorios"]) for o in offices.values()]),
        "metadados": meta,
    }
