import asyncio
import datetime
from pathlib import Path
from typing import Any
from .identity import HOUSE_LABELS, resolve_parliamentarian
from .utils import (
    cache_max_age,
    cached_file_age_hours,
    fetch_all_pages,
    fetch_cached_file,
    iter_csv_rows,
    make_request,
)

SENADO_BASE_URL = "https://legis.senado.leg.br/dadosabertos"
CAMARA_BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
CAMARA_VOTOS_BULK_URL = "https://dadosabertos.camara.leg.br/arquivos/votacoesVotos/csv/votacoesVotos-{year}.csv"
CAMARA_VOTACOES_BULK_URL = "https://dadosabertos.camara.leg.br/arquivos/votacoes/csv/votacoes-{year}.csv"

# Senate per-senator vote codes. Absences are published explicitly, which lets us separate
# justified absences (official mission, leave) from plain non-attendance.
SENADO_VOTE_CODES = {
    "AP": "Atividade parlamentar (ausência justificada)",
    "MIS": "Missão oficial (ausência justificada)",
    "REP": "Representação da Casa (ausência justificada)",
    "LS": "Licença saúde",
    "LP": "Licença particular",
    "NCom": "Não compareceu",
    "P-NRV": "Presente, não registrou voto",
    "P-OD": "Presente, obstrução declarada",
    "Votou": "Votou (votação secreta)",
}
SENADO_VOTED = {"Sim", "Não", "Abstenção", "Votou"}
SENADO_PRESENT_NO_VOTE = {"P-NRV", "P-OD"}
SENADO_JUSTIFIED_ABSENCE = {"AP", "MIS", "REP"}
SENADO_UNJUSTIFIED_ABSENCE = {"NCom"}

# Positions a voter can actually read from the record.
KNOWN_POSITIONS = {"Sim", "Não", "Abstenção", "Obstrução", "P-OD"}

_camara_votacoes_cache: dict[tuple[int, float], dict[str, dict[str, str]]] = {}
_camara_votos_cache: dict[tuple[int, int, float], tuple[set[str], list[dict[str, str]], dict[str, int]]] = {}


def _period(start_date: str | None, end_date: str | None) -> tuple[str, str]:
    """Default to the current calendar year so results never depend on an API's hidden window."""
    today = datetime.date.today()
    return start_date or f"{today.year}-01-01", end_date or today.isoformat()


async def _get_deputy_activity(
    deputy: dict[str, Any],
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    dep_id = deputy["id"]
    start, end = _period(start_date, end_date)
    url = f"{CAMARA_BASE_URL}/deputados/{dep_id}/eventos"
    events_raw, complete = await fetch_all_pages(
        url,
        params={"dataInicio": start, "dataFim": end, "ordem": "DESC", "ordenarPor": "dataHoraInicio"},
    )

    types_summary: dict[str, int] = {}
    events: list[dict[str, Any]] = []

    for ev in events_raw:
        ev_type = ev.get("descricaoTipo") or "Outro"
        types_summary[ev_type] = types_summary.get(ev_type, 0) + 1

        events.append({
            "id": ev.get("id"),
            "data_inicio": ev.get("dataHoraInicio"),
            "data_fim": ev.get("dataHoraFim"),
            "tipo": ev_type,
            "situacao": ev.get("situacao"),
            "descricao": ev.get("descricao"),
            "orgaos": [o.get("siglaOrgao") for o in ev.get("orgaos", []) if o.get("siglaOrgao")],
        })

    limitacoes = ["Eventos em cuja pauta o deputado constava."]
    if not complete:
        limitacoes.append("Paginação incompleta: a contagem de eventos pode estar SUBESTIMADA.")

    return {
        "parlamentar": deputy["nome"],
        "id": dep_id,
        "casa": HOUSE_LABELS["camara"],
        "total_eventos": len(events_raw),
        "eventos_por_tipo": types_summary,
        "eventos": events[:limit],
        "metadados": {
            "fonte": url,
            "periodo": f"{start} a {end}",
            "n_registros": len(events_raw),
            "completo": complete,
            "limitacoes": limitacoes,
        },
    }


async def _get_senator_activity(
    senator: dict[str, Any],
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    code = senator["id"]
    start, end = _period(start_date, end_date)
    speeches_url = f"{SENADO_BASE_URL}/senador/{code}/discursos"
    comm_url = f"{SENADO_BASE_URL}/senador/{code}/comissoes"

    speeches_data, comm_data = await asyncio.gather(
        make_request(speeches_url, params={"dataInicio": start.replace("-", ""), "dataFim": end.replace("-", "")}),
        make_request(comm_url),
    )
    speeches_raw = (
        (speeches_data or {}).get("DiscursosParlamentar", {})
        .get("Parlamentar", {})
        .get("Pronunciamentos", {})
        .get("Pronunciamento", [])
    )
    if isinstance(speeches_raw, dict):
        speeches_raw = [speeches_raw]

    speeches: list[dict[str, Any]] = []
    for sp in speeches_raw:
        tipo = sp.get("TipoUsoPalavra")
        speeches.append({
            "data": sp.get("DataPronunciamento"),
            "tipo": tipo.get("Descricao") if isinstance(tipo, dict) else sp.get("TipoSessao"),
            "resumo": sp.get("TextoResumo"),
            "casa": sp.get("SiglaCasaPronunciamento", "SF"),
        })

    comm_list = (
        (comm_data or {}).get("MembroComissaoParlamentar", {})
        .get("Parlamentar", {})
        .get("MembroComissoes", {})
        .get("Comissao", [])
    )
    if isinstance(comm_list, dict):
        comm_list = [comm_list]

    comm_summary = [
        {
            "comissao": c.get("IdentificacaoComissao", {}).get("NomeComissao"),
            "sigla": c.get("IdentificacaoComissao", {}).get("SiglaComissao"),
            "cargo": c.get("DescricaoParticipacao"),
        }
        for c in comm_list
    ]

    return {
        "parlamentar": senator["nome"],
        "id": code,
        "casa": HOUSE_LABELS["senado"],
        "total_discursos": len(speeches_raw),
        "total_comissoes_ativas": len(comm_summary),
        "discursos_recentes": speeches[:limit],
        "comissoes_atuais": comm_summary[:10],
        "metadados": {
            "fonte": speeches_url,
            "periodo": f"{start} a {end}",
            "n_registros": len(speeches_raw),
            "completo": speeches_data is not None,
            "limitacoes": ["Comissões incluem participações históricas."],
        },
    }


def _classify_senate_vote(code: str) -> str:
    if code in SENADO_VOTED or code.startswith("Presidente"):
        return "votou"
    if code in SENADO_PRESENT_NO_VOTE:
        return "presente_sem_votar"
    if code in SENADO_JUSTIFIED_ABSENCE or code.startswith("L"):
        return "ausencia_justificada"
    if code in SENADO_UNJUSTIFIED_ABSENCE:
        return "ausencia_nao_justificada"
    return "outros"


async def _fetch_senator_votes(code: int, year: int) -> list[dict[str, Any]] | None:
    data = await make_request(
        f"{SENADO_BASE_URL}/votacao",
        params={"codigoParlamentar": code, "dataInicio": f"{year}-01-01", "dataFim": f"{year}-12-31"},
    )
    return data if isinstance(data, list) else None


async def _get_senator_votes(
    senator: dict[str, Any],
    limit: int = 15,
    year: int | None = None,
) -> dict[str, Any]:
    code = senator["id"]
    target_year = year or datetime.date.today().year

    votacoes_raw = await _fetch_senator_votes(code, target_year)
    if not votacoes_raw and year is None:
        target_year -= 1
        votacoes_raw = await _fetch_senator_votes(code, target_year)
    complete = votacoes_raw is not None
    votacoes_raw = votacoes_raw or []

    votes: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    participation = {
        "votou": 0,
        "presente_sem_votar": 0,
        "ausencia_justificada": 0,
        "ausencia_nao_justificada": 0,
        "outros": 0,
    }

    for v in votacoes_raw:
        own = next((x for x in v.get("votos", []) if str(x.get("codigoParlamentar")) == str(code)), None)
        raw_vote = (own or {}).get("siglaVotoParlamentar") or "Não registrado"
        positions[raw_vote] = positions.get(raw_vote, 0) + 1
        participation[_classify_senate_vote(raw_vote)] += 1

        votes.append({
            "materia": v.get("identificacao"),
            "ementa": v.get("ementa"),
            "data": v.get("dataSessao"),
            "voto": raw_vote,
            "significado_voto": SENADO_VOTE_CODES.get(raw_vote),
            "descricao_votacao": v.get("descricaoVotacao"),
            "votacao_secreta": v.get("votacaoSecreta") == "S",
            "resultado": v.get("resultadoVotacao"),
            "codigo_sessao_votacao": v.get("codigoSessaoVotacao"),
        })

    votes.sort(key=lambda x: str(x.get("data") or ""), reverse=True)
    total = len(votacoes_raw)

    return {
        "parlamentar": senator["nome"],
        "id": code,
        "casa": HOUSE_LABELS["senado"],
        "ano_referencia": target_year,
        "total_votacoes_encontradas": total,
        "distribuicao_votos": positions,
        "participacao_plenario": {
            "votacoes_nominais": total,
            **participation,
            "votacoes_secretas": sum(1 for v in votes if v["votacao_secreta"]),
            "distribuicao_plenario": dict(positions),
        },
        "votacoes": votes[:limit],
        "metadados": {
            "fonte": f"{SENADO_BASE_URL}/votacao?codigoParlamentar={code}",
            "periodo": str(target_year),
            "n_registros": total,
            "completo": complete,
            "limitacoes": [
                "Filtro pela data da sessão (não pelo ano da matéria).",
                "Votações secretas registram apenas 'Votou', sem a posição.",
            ],
        },
    }


def _scan_camara_votacoes(path: Path) -> dict[str, dict[str, str]]:
    return {
        row["id"]: {
            "orgao": row.get("siglaOrgao", ""),
            "data": row.get("dataHoraRegistro") or row.get("data", ""),
            "descricao": row.get("descricao", ""),
        }
        for row in iter_csv_rows(path)
        if row.get("id")
    }


def _scan_camara_votos(
    path: Path, dep_id: int, plenary_ids: set[str]
) -> tuple[set[str], list[dict[str, str]], dict[str, int]]:
    """
    One pass over the yearly votes file: every nominal roll call id, this deputy's votes,
    and, for the house reference, each deputy's count of plenary votes with a known position.
    """
    target = str(dep_id)
    all_ids: set[str] = set()
    own: list[dict[str, str]] = []
    known_per_deputy: dict[str, int] = {}
    for row in iter_csv_rows(path):
        vid = row.get("idVotacao")
        if not vid:
            continue
        all_ids.add(vid)
        voter = row.get("deputado_id") or ""
        if vid in plenary_ids and row.get("voto") in KNOWN_POSITIONS:
            known_per_deputy[voter] = known_per_deputy.get(voter, 0) + 1
        if voter == target:
            own.append({"idVotacao": vid, "voto": row.get("voto") or "", "dataHoraVoto": row.get("dataHoraVoto") or ""})
    return all_ids, own, known_per_deputy


async def _load_camara_votes(
    dep_id: int, year: int
) -> tuple[dict[str, dict[str, str]], set[str], list[dict[str, str]], dict[str, int], dict[str, Any]] | None:
    max_age = cache_max_age(year)
    votos_url = CAMARA_VOTOS_BULK_URL.format(year=year)
    votacoes_url = CAMARA_VOTACOES_BULK_URL.format(year=year)
    votos_path, votacoes_path = await asyncio.gather(
        fetch_cached_file(votos_url, f"camara/votacoesVotos-{year}.csv", max_age),
        fetch_cached_file(votacoes_url, f"camara/votacoes-{year}.csv", max_age),
    )
    if not votos_path or not votacoes_path:
        return None

    vkey = (year, votacoes_path.stat().st_mtime)
    if vkey not in _camara_votacoes_cache:
        _camara_votacoes_cache[vkey] = await asyncio.to_thread(_scan_camara_votacoes, votacoes_path)

    votacoes = _camara_votacoes_cache[vkey]
    plenary_orgao_ids = {vid for vid, info in votacoes.items() if info.get("orgao") == "PLEN"}

    dkey = (year, dep_id, votos_path.stat().st_mtime)
    if dkey not in _camara_votos_cache:
        _camara_votos_cache[dkey] = await asyncio.to_thread(_scan_camara_votos, votos_path, dep_id, plenary_orgao_ids)

    all_ids, own, known_per_deputy = _camara_votos_cache[dkey]
    meta = {"fonte": votos_url, "fonte_votacoes": votacoes_url, "idade_arquivo_horas": cached_file_age_hours(votos_path)}
    return votacoes, all_ids, own, known_per_deputy, meta


async def _get_deputy_votes(
    deputy: dict[str, Any],
    limit: int = 15,
    year: int | None = None,
) -> dict[str, Any]:
    dep_id = deputy["id"]
    target_year = year or datetime.date.today().year

    loaded = await _load_camara_votes(dep_id, target_year)
    if (loaded is None or not loaded[1]) and year is None:
        target_year -= 1
        loaded = await _load_camara_votes(dep_id, target_year)

    if loaded is None:
        return {
            "parlamentar": deputy["nome"],
            "id": dep_id,
            "casa": HOUSE_LABELS["camara"],
            "ano_referencia": target_year,
            "error": "Arquivo anual de votações da Câmara indisponível; tente novamente mais tarde.",
        }

    votacoes, all_ids, own, known_per_deputy, meta = loaded
    plenary_ids = {vid for vid in all_ids if votacoes.get(vid, {}).get("orgao") == "PLEN"}

    positions: dict[str, int] = {}
    plenary_positions: dict[str, int] = {}
    voted_plenary: set[str] = set()
    votes: list[dict[str, Any]] = []
    for v in own:
        vid = v["idVotacao"]
        pos = v["voto"] or "Não registrado"
        positions[pos] = positions.get(pos, 0) + 1
        info = votacoes.get(vid, {})
        if vid in plenary_ids:
            voted_plenary.add(vid)
            plenary_positions[pos] = plenary_positions.get(pos, 0) + 1
        votes.append({
            "votacao_id": vid,
            "data": info.get("data") or v["dataHoraVoto"],
            "orgao": info.get("orgao"),
            "descricao": info.get("descricao"),
            "voto": pos,
            "url": f"{CAMARA_BASE_URL}/votacoes/{vid}",
        })

    votes.sort(key=lambda x: str(x.get("data") or ""), reverse=True)
    total_plenary = len(plenary_ids)
    voted = len(voted_plenary)

    return {
        "parlamentar": deputy["nome"],
        "id": dep_id,
        "casa": HOUSE_LABELS["camara"],
        "ano_referencia": target_year,
        "total_votacoes_encontradas": len(own),
        "distribuicao_votos": positions,
        "participacao_plenario": {
            "votacoes_nominais": total_plenary,
            "votou": voted,
            "sem_registro_de_voto": total_plenary - voted,
            "distribuicao_plenario": plenary_positions,
        },
        "votacoes": votes[:limit],
        "metadados": {
            **meta,
            "periodo": str(target_year),
            "n_registros": len(own),
            "completo": True,
            "limitacoes": [
                "Participação calculada sobre todas as votações nominais do Plenário no ano.",
                "A Câmara não publica o motivo da ausência: 'sem_registro_de_voto' inclui licenças, "
                "missões oficiais e períodos em que o deputado não estava em exercício.",
                "'Artigo 17' indica o Presidente da Câmara, que não vota (conta como presença).",
            ],
        },
    }


async def get_parliamentarian_activity(
    name: str | None = None,
    house: str = "auto",
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
    parliamentarian_id: int | None = None,
) -> dict[str, Any]:
    """
    Retrieve parliamentary events, presence, plenary sessions, or speeches.
    """
    parl = await resolve_parliamentarian(name, house=house, parliamentarian_id=parliamentarian_id)
    if "error" in parl:
        return parl

    if parl["casa"] == "senado":
        return await _get_senator_activity(parl, start_date=start_date, end_date=end_date, limit=limit)
    return await _get_deputy_activity(parl, start_date=start_date, end_date=end_date, limit=limit)


async def get_parliamentarian_votes(
    name: str | None = None,
    house: str = "auto",
    limit: int = 15,
    year: int | None = None,
    parliamentarian_id: int | None = None,
) -> dict[str, Any]:
    """
    Retrieve nominal roll-call voting history for a Brazilian parliamentarian.
    """
    parl = await resolve_parliamentarian(name, house=house, parliamentarian_id=parliamentarian_id)
    if "error" in parl:
        return parl

    if parl["casa"] == "senado":
        return await _get_senator_votes(parl, limit=limit, year=year)
    return await _get_deputy_votes(parl, limit=limit, year=year)
