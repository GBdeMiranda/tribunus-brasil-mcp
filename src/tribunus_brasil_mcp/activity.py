import datetime
import urllib.parse
from typing import Any
from .utils import make_request

SENADO_BASE_URL = "https://legis.senado.leg.br/dadosabertos"
CAMARA_BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"


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


async def _get_deputy_activity(
    deputy: dict[str, Any],
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    dep_id = deputy.get("id")
    params: dict[str, Any] = {
        "itens": 50,
        "ordem": "DESC",
        "ordenarPor": "dataHoraInicio",
    }
    if start_date:
        params["dataInicio"] = start_date
    if end_date:
        params["dataFim"] = end_date

    url = f"{CAMARA_BASE_URL}/deputados/{dep_id}/eventos"
    data = await make_request(url, params=params) or {}
    events_raw = data.get("dados", [])

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

    return {
        "parlamentar": deputy.get("nome"),
        "casa": "Câmara dos Deputados",
        "total_eventos": len(events_raw),
        "eventos_por_tipo": types_summary,
        "eventos": events[:limit],
    }


async def _get_senator_activity(
    senator: dict[str, Any],
    limit: int = 20,
) -> dict[str, Any]:
    code = senator.get("CodigoParlamentar")
    speeches_data = await make_request(f"{SENADO_BASE_URL}/senador/{code}/discursos") or {}
    speeches_raw = (
        speeches_data.get("DiscursosParlamentar", {})
        .get("Parlamentar", {})
        .get("Pronunciamentos", {})
        .get("Pronunciamento", [])
    )
    if isinstance(speeches_raw, dict):
        speeches_raw = [speeches_raw]

    speeches: list[dict[str, Any]] = []
    for sp in speeches_raw:
        speeches.append({
            "data": sp.get("DataPronunciamento"),
            "tipo": sp.get("TipoSessao"),
            "resumo": sp.get("TextoResumo"),
            "casa": sp.get("SiglaCasaPronunciamento", "SF"),
        })

    # Get active committees
    comm_data = await make_request(f"{SENADO_BASE_URL}/senador/{code}/comissoes") or {}
    comm_list = (
        comm_data.get("MembroComissaoParlamentar", {})
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
        "parlamentar": senator.get("NomeParlamentar"),
        "casa": "Senado Federal",
        "total_discursos": len(speeches_raw),
        "total_comissoes_ativas": len(comm_summary),
        "discursos_recentes": speeches[:limit],
        "comissoes_atuais": comm_summary[:10],
    }


async def _get_senator_votes(
    senator: dict[str, Any],
    limit: int = 15,
    year: int | None = None,
) -> dict[str, Any]:
    code = senator.get("CodigoParlamentar")
    votes_data = await make_request(f"{SENADO_BASE_URL}/senador/{code}/votacoes") or {}
    votacoes_raw = (
        votes_data.get("VotacaoParlamentar", {})
        .get("Parlamentar", {})
        .get("Votacoes", {})
        .get("Votacao", [])
    )
    if isinstance(votacoes_raw, dict):
        votacoes_raw = [votacoes_raw]

    votes: list[dict[str, Any]] = []
    positions: dict[str, int] = {}

    for v in votacoes_raw:
        materia_info = v.get("Materia", {})
        mat_year = materia_info.get("Ano")
        if year and mat_year and str(mat_year) != str(year):
            continue

        raw_vote = v.get("DescricaoVoto", "").replace(" (Voto Secreto)", "").strip() or "Não registrado"
        positions[raw_vote] = positions.get(raw_vote, 0) + 1

        sessao = v.get("SessaoPlenaria", {})
        data_sessao = sessao.get("DataSessao") if isinstance(sessao, dict) else None

        votes.append({
            "materia": f"{materia_info.get('Sigla', '')} {materia_info.get('Numero', '')}/{materia_info.get('Ano', '')}".strip(),
            "data": data_sessao,
            "voto": raw_vote,
            "descricao_votacao": v.get("DescricaoVotacao"),
        })

    return {
        "parlamentar": senator.get("NomeParlamentar"),
        "casa": "Senado Federal",
        "total_votacoes_encontradas": len(votes),
        "distribuicao_votos": positions,
        "votacoes": votes[:limit],
    }


async def _get_deputy_votes(
    deputy: dict[str, Any],
    limit: int = 15,
) -> dict[str, Any]:
    dep_id = deputy.get("id")
    # Query recent roll-call votes from Chamber
    rollcalls_data = await make_request(
        f"{CAMARA_BASE_URL}/votacoes?ordem=DESC&ordenarPor=dataHoraRegistro&itens=10"
    ) or {}
    recent_rollcalls = rollcalls_data.get("dados", [])

    matched_votes: list[dict[str, Any]] = []
    positions: dict[str, int] = {}

    for rc in recent_rollcalls:
        rc_id = rc.get("id")
        if not rc_id:
            continue
        votos_data = await make_request(f"{CAMARA_BASE_URL}/votacoes/{rc_id}/votos") or {}
        votos_list = votos_data.get("dados", [])
        for v in votos_list:
            dep_info = v.get("deputado_", {})
            if str(dep_info.get("id")) == str(dep_id):
                pos = v.get("tipoVoto", "Registrado")
                positions[pos] = positions.get(pos, 0) + 1
                matched_votes.append({
                    "votacao_id": rc_id,
                    "data": rc.get("dataHoraRegistro"),
                    "proposicao": rc.get("proposicaoObjeto") or rc.get("descricao"),
                    "voto": pos,
                })
                break
        if len(matched_votes) >= limit:
            break

    return {
        "parlamentar": deputy.get("nome"),
        "casa": "Câmara dos Deputados",
        "total_votacoes_encontradas": len(matched_votes),
        "distribuicao_votos": positions,
        "votacoes": matched_votes,
    }


async def get_parliamentarian_activity(
    name: str,
    house: str = "auto",
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Retrieve parliamentary events, presence, plenary sessions, or speeches.
    """
    target_house = house.lower()

    if target_house in ("auto", "senado"):
        senator = await _find_senator_info(name)
        if senator:
            return await _get_senator_activity(senator, limit=limit)

    if target_house in ("auto", "camara"):
        deputy = await _find_deputy_info(name)
        if deputy:
            return await _get_deputy_activity(
                deputy, start_date=start_date, end_date=end_date, limit=limit
            )

    return {"error": f"Parliamentarian '{name}' not found in {house}."}


async def get_parliamentarian_votes(
    name: str,
    house: str = "auto",
    limit: int = 15,
    year: int | None = None,
) -> dict[str, Any]:
    """
    Retrieve nominal roll-call voting history for a Brazilian parliamentarian.
    """
    target_house = house.lower()

    if target_house in ("auto", "senado"):
        senator = await _find_senator_info(name)
        if senator:
            return await _get_senator_votes(senator, limit=limit, year=year)

    if target_house in ("auto", "camara"):
        deputy = await _find_deputy_info(name)
        if deputy:
            return await _get_deputy_votes(deputy, limit=limit)

    return {"error": f"Parliamentarian '{name}' not found in {house}."}
