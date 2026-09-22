import asyncio
import urllib.parse
from typing import Any
from .utils import make_request

SENADO_BASE_URL = "https://legis.senado.leg.br/dadosabertos"
CAMARA_BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"


async def _fetch_thematic_bills(
    theme: str, house: str = "auto", limit: int = 5, year: int | None = None
) -> list[dict[str, Any]]:
    """Fetch legislative proposals matching the keyword across Senate and Chamber."""
    bills: list[dict[str, Any]] = []
    target_house = house.lower()

    # 1. Senate bills
    if target_house in ("auto", "senado"):
        sen_params: dict[str, Any] = {"palavraChave": theme}
        if year:
            sen_params["ano"] = str(year)
        data = await make_request(f"{SENADO_BASE_URL}/materia/pesquisa/lista", params=sen_params) or {}
        materias = data.get("PesquisaBasicaMateria", {}).get("Materias", {}).get("Materia", [])
        if isinstance(materias, dict):
            materias = [materias]
        for m in materias[:limit]:
            bills.append({
                "casa": "Senado Federal",
                "codigo": m.get("Codigo"),
                "identificacao": m.get("DescricaoIdentificacao"),
                "autor": m.get("Autor"),
                "ementa": m.get("Ementa"),
            })

    # 2. Chamber bills
    if target_house in ("auto", "camara"):
        cam_params: dict[str, Any] = {
            "keywords": theme,
            "itens": limit,
            "ordem": "DESC",
            "ordenarPor": "id",
        }
        if year:
            cam_params["ano"] = year
        data = await make_request(f"{CAMARA_BASE_URL}/proposicoes", params=cam_params) or {}
        props = data.get("dados", [])
        for p in props[:limit]:
            bills.append({
                "casa": "Câmara dos Deputados",
                "codigo": str(p.get("id")),
                "identificacao": f"{p.get('siglaTipo', '')} {p.get('numero', '')}/{p.get('ano', '')}".strip(),
                "autor": None,
                "ementa": p.get("ementa"),
            })

    return bills[: limit * 2 if target_house == "auto" else limit]


async def _fetch_thematic_speeches(
    theme: str, house: str = "auto", limit: int = 5
) -> list[dict[str, Any]]:
    """Fetch plenary speeches mentioning the policy theme."""
    speeches: list[dict[str, Any]] = []
    keyword_clean = theme.lower().strip()
    target_house = house.lower()

    if target_house in ("auto", "senado"):
        data = await make_request(f"{SENADO_BASE_URL}/plenario/lista/discursos") or {}
        discursos_raw = (
            data.get("ListaDiscursos", {})
            .get("Discursos", {})
            .get("Discurso", [])
        )
        if isinstance(discursos_raw, dict):
            discursos_raw = [discursos_raw]
        for d in discursos_raw:
            resumo = f"{d.get('TextoResumo', '')} {d.get('Indexacao', '')}".lower()
            if keyword_clean in resumo:
                speeches.append({
                    "casa": "Senado Federal",
                    "parlamentar": d.get("NomeParlamentar"),
                    "partido": d.get("SiglaPartidoParlamentar"),
                    "data": d.get("DataDiscurso"),
                    "resumo": d.get("TextoResumo"),
                })
            if len(speeches) >= limit:
                break

    return speeches[:limit]


async def _fetch_thematic_hearings(
    theme: str, house: str = "auto", limit: int = 5
) -> list[dict[str, Any]]:
    """Fetch committee meetings, public hearings, and agendas reviewing the theme."""
    hearings: list[dict[str, Any]] = []
    keyword_clean = theme.lower().strip()
    target_house = house.lower()

    if target_house in ("auto", "camara"):
        data = await make_request(
            f"{CAMARA_BASE_URL}/eventos?itens=30&ordem=DESC&ordenarPor=dataHoraInicio"
        ) or {}
        events_raw = data.get("dados", [])
        for ev in events_raw:
            desc = f"{ev.get('descricao', '')} {ev.get('descricaoTipo', '')}".lower()
            if keyword_clean in desc:
                orgaos = [o.get("siglaOrgao") for o in ev.get("orgaos", []) if o.get("siglaOrgao")]
                hearings.append({
                    "casa": "Câmara dos Deputados",
                    "tipo": ev.get("descricaoTipo"),
                    "situacao": ev.get("situacao"),
                    "data_inicio": ev.get("dataHoraInicio"),
                    "descricao": ev.get("descricao"),
                    "comissoes": orgaos,
                })
            if len(hearings) >= limit:
                break

    return hearings[:limit]


async def _fetch_thematic_votes(
    theme: str, house: str = "auto", limit: int = 5
) -> list[dict[str, Any]]:
    """Fetch recent roll-call votes directly affecting or mentioning the domain."""
    votes: list[dict[str, Any]] = []
    keyword_clean = theme.lower().strip()
    target_house = house.lower()

    if target_house in ("auto", "camara"):
        data = await make_request(
            f"{CAMARA_BASE_URL}/votacoes?itens=30&ordem=DESC&ordenarPor=dataHoraRegistro"
        ) or {}
        votacoes_raw = data.get("dados", [])
        for v in votacoes_raw:
            desc = f"{v.get('proposicaoObjeto', '')} {v.get('descricao', '')}".lower()
            if keyword_clean in desc:
                votes.append({
                    "casa": "Câmara dos Deputados",
                    "votacao_id": v.get("id"),
                    "data": v.get("dataHoraRegistro"),
                    "proposicao": v.get("proposicaoObjeto"),
                    "descricao": v.get("descricao"),
                    "aprovacao": v.get("aprovacao"),
                })
            if len(votes) >= limit:
                break

    return votes[:limit]


def _generate_thematic_summary(
    theme: str,
    bills: list[dict[str, Any]],
    speeches: list[dict[str, Any]],
    hearings: list[dict[str, Any]],
    votes: list[dict[str, Any]],
) -> str:
    """Generate an objective executive synthesis of congressional momentum on the topic."""
    total_items = len(bills) + len(speeches) + len(hearings) + len(votes)
    if total_items == 0:
        return (
            f"Não foram encontradas deliberações recentes com correspondência direta ao termo '{theme}' "
            f"nas proposições, discursos, reuniões de comissão ou votações amostradas."
        )

    sentences = [
        f"O tema '{theme}' registra atividade legislativa com {len(bills)} proposição(ões) catalogada(s), "
        f"{len(speeches)} pronunciamento(s), {len(hearings)} evento(s)/audiência(s) e {len(votes)} votação(ões) recente(s)."
    ]

    if bills:
        bill_names = [b.get("identificacao", "PL") for b in bills[:3] if b.get("identificacao")]
        sentences.append(f"Dentre as matérias em trâmite destacam-se: {', '.join(bill_names)}.")

    if hearings:
        comissoes = set()
        for h in hearings:
            comissoes.update(h.get("comissoes", []))
        if comissoes:
            sentences.append(f"O debate ocorre ativamente nos seguintes colegiados: {', '.join(sorted(comissoes))}.")

    if votes:
        sentences.append(f"Há deliberações plenárias recentes com votações registradas sobre a matéria.")

    return " ".join(sentences)


async def search_congressional_themes(
    theme: str,
    house: str = "auto",
    limit: int = 5,
    year: int | None = None,
) -> dict[str, Any]:
    """
    Synthesize congressional activity surrounding a specific public policy topic.
    """
    clean_theme = theme.strip()
    if not clean_theme:
        return {"error": "A valid thematic keyword must be provided."}

    bills_task = asyncio.create_task(_fetch_thematic_bills(clean_theme, house=house, limit=limit, year=year))
    speeches_task = asyncio.create_task(_fetch_thematic_speeches(clean_theme, house=house, limit=limit))
    hearings_task = asyncio.create_task(_fetch_thematic_hearings(clean_theme, house=house, limit=limit))
    votes_task = asyncio.create_task(_fetch_thematic_votes(clean_theme, house=house, limit=limit))

    bills, speeches, hearings, votes = await asyncio.gather(
        bills_task, speeches_task, hearings_task, votes_task
    )

    resumo = _generate_thematic_summary(clean_theme, bills, speeches, hearings, votes)

    return {
        "tema": clean_theme,
        "casa_filtro": house,
        "limite_por_categoria": limit,
        "resumo_executivo": resumo,
        "metricas": {
            "total_proposicoes": len(bills),
            "total_discursos": len(speeches),
            "total_audiencias_comissoes": len(hearings),
            "total_votacoes": len(votes),
        },
        "proposicoes": bills,
        "pronunciamentos_discursos": speeches,
        "audiencias_comissoes": hearings,
        "votacoes_recentes": votes,
    }
