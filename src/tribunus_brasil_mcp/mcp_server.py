import json
import sys
import urllib.parse
from mcp.server.fastmcp import FastMCP

from .utils import make_request, fetch_document_content, extract_text_from_pdf
from .transparency import get_parliamentarian_expenses
from .activity import get_parliamentarian_activity, get_parliamentarian_votes
from .evaluation import evaluate_parliamentarian
from .themes import search_congressional_themes

mcp = FastMCP("tribunus-brasil-mcp")

SENADO_BASE_URL = "https://legis.senado.leg.br/dadosabertos"
CAMARA_BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
API_BASE_URL = SENADO_BASE_URL


def _find_document_url(data: list | dict | None) -> str | None:
    """Extract the first document URL found in the API response."""
    if isinstance(data, list) and data:
        return data[0].get("urlDocumento")
    if isinstance(data, dict):
        return data.get("urlDocumento")
    return None


async def _get_senator(name: str, include_votes: bool = False, limit: int = 5) -> dict | None:
    """Fetch senator details, committees, and authored bills from Senate open data."""
    search_name = name.lower()
    data = await make_request(f"{SENADO_BASE_URL}/senador/lista/atual") or {}
    senators = data.get("ListaParlamentarEmExercicio", {}).get("Parlamentares", {}).get("Parlamentar", [])
    if isinstance(senators, dict):
        senators = [senators]

    found = None
    for s in senators:
        info = s.get("IdentificacaoParlamentar", {})
        full = info.get("NomeCompletoParlamentar", "").lower()
        parl = info.get("NomeParlamentar", "").lower()
        if search_name in full or search_name in parl:
            found = info
            break

    if not found:
        return None

    code = found.get("CodigoParlamentar")

    # Fetch active committees
    comm_data = await make_request(f"{SENADO_BASE_URL}/senador/{code}/comissoes") or {}
    comm_list = comm_data.get("MembroComissaoParlamentar", {}).get("Parlamentar", {}).get("MembroComissoes", {}).get("Comissao", [])
    if isinstance(comm_list, dict):
        comm_list = [comm_list]
    comissoes = [
        {
            "nome": c.get("IdentificacaoComissao", {}).get("NomeComissao"),
            "sigla": c.get("IdentificacaoComissao", {}).get("SiglaComissao"),
            "cargo": c.get("DescricaoParticipacao"),
        }
        for c in comm_list[:limit]
    ]

    # Fetch authored proposals
    aut_data = await make_request(f"{SENADO_BASE_URL}/senador/{code}/autorias") or {}
    aut_list = aut_data.get("MateriasAutoriaParlamentar", {}).get("Parlamentar", {}).get("Autorias", {}).get("Autoria", [])
    if isinstance(aut_list, dict):
        aut_list = [aut_list]
    proposicoes = [
        {
            "identificacao": a.get("Materia", {}).get("IdentificacaoProcesso"),
            "ementa": a.get("Materia", {}).get("Ementa"),
        }
        for a in aut_list[:limit]
    ]

    profile = {
        "parlamentar": found.get("NomeParlamentar"),
        "nome_civil": found.get("NomeCompletoParlamentar"),
        "casa": "Senado Federal",
        "partido": found.get("SiglaPartidoParlamentar"),
        "uf": found.get("UfParlamentar"),
        "email": found.get("EmailParlamentar"),
        "foto": found.get("UrlFotoParlamentar"),
        "situacao": "Em Exercício",
        "codigo": code,
        "comissoes": comissoes,
        "proposicoes_autoria": proposicoes,
    }

    if include_votes:
        votes_data = await make_request(f"{SENADO_BASE_URL}/senador/{code}/votacoes") or {}
        votacoes = votes_data.get("VotacaoParlamentar", {}).get("Parlamentar", {}).get("Votacoes", {}).get("Votacao", [])
        if isinstance(votacoes, dict):
            votacoes = [votacoes]
        profile["votacoes"] = [
            {
                "materia": f"{v.get('Materia', {}).get('Sigla', '')} {v.get('Materia', {}).get('Numero', '')}/{v.get('Materia', {}).get('Ano', '')}".strip(),
                "voto": v.get("DescricaoVoto", "").replace(" (Voto Secreto)", "").strip(),
                "descricao": v.get("DescricaoVotacao"),
            }
            for v in votacoes[:limit]
        ]

    return profile


async def _get_deputy(name: str, include_votes: bool = False, limit: int = 5) -> dict | None:
    """Fetch deputy details, committees, and authored bills from Chamber of Deputies open data."""
    encoded_name = urllib.parse.quote(name)
    search_data = await make_request(f"{CAMARA_BASE_URL}/deputados?nome={encoded_name}") or {}
    deputados = search_data.get("dados", [])
    if not deputados:
        return None

    dep = deputados[0]
    dep_id = dep.get("id")

    detail_data = await make_request(f"{CAMARA_BASE_URL}/deputados/{dep_id}") or {}
    d = detail_data.get("dados", {})
    ultimo = d.get("ultimoStatus", {})

    orgaos_data = await make_request(f"{CAMARA_BASE_URL}/deputados/{dep_id}/orgaos") or {}
    comissoes = [
        {
            "nome": o.get("nomeOrgao"),
            "sigla": o.get("siglaOrgao"),
            "cargo": o.get("titulo"),
        }
        for o in orgaos_data.get("dados", [])[:limit]
    ]

    proposicoes_data = await make_request(f"{CAMARA_BASE_URL}/proposicoes?idDeputadoAutor={dep_id}&itens={limit}") or {}
    proposicoes = [
        {
            "identificacao": f"{p.get('siglaTipo')} {p.get('numero')}/{p.get('ano')}",
            "ementa": p.get("ementa"),
        }
        for p in proposicoes_data.get("dados", [])
    ]

    profile = {
        "parlamentar": dep.get("nome"),
        "nome_civil": d.get("nomeCivil"),
        "casa": "Câmara dos Deputados",
        "partido": ultimo.get("siglaPartido"),
        "uf": ultimo.get("siglaUf"),
        "email": ultimo.get("email") or dep.get("email"),
        "foto": ultimo.get("urlFoto") or dep.get("urlFoto"),
        "situacao": ultimo.get("situacao", "Exercício"),
        "codigo": dep_id,
        "comissoes": comissoes,
        "proposicoes_autoria": proposicoes,
    }

    if include_votes:
        profile["votacoes"] = []

    return profile


@mcp.tool()
async def getParliamentarianProfile(
    name: str,
    house: str = "auto",
    includeVotes: bool = False,
    limit: int = 5,
) -> str:
    """
    Get the unified profile of a Brazilian parliamentarian (Senator or Federal Deputy).

    Consolidates biographical data, political party, state (UF), active committee assignments,
    authored legislative proposals, and nominal voting records into a single standardized JSON format.

    Args:
        name: Name of the parliamentarian (e.g., 'Arthur Lira', 'Rodrigo Pacheco', 'Tabata Amaral').
        house: Legislative house to search. Options: 'auto' (searches both), 'senado' (Senate), or 'camara' (Chamber of Deputies). Default: 'auto'.
        includeVotes: Whether to include nominal voting history (default: False).
        limit: Maximum number of committees and authored proposals to return (default: 5).
    """
    profile = None
    target_house = house.lower()

    if target_house in ("auto", "senado"):
        profile = await _get_senator(name, include_votes=includeVotes, limit=limit)

    if not profile and target_house in ("auto", "camara"):
        profile = await _get_deputy(name, include_votes=includeVotes, limit=limit)

    if not profile:
        return json.dumps(
            {"error": f"Parliamentarian '{name}' not found in {house}."},
            ensure_ascii=False,
            indent=2,
        )

    return json.dumps(profile, ensure_ascii=False, indent=2)


@mcp.tool()
async def searchLegislativeProposals(keyword: str = "", year: int | None = None, limit: int = 10) -> str:
    """
    Search for specific legislative bills and proposals in the Brazilian Congress by exact keyword or number.
    
    Use this tool when you need to find a specific bill (e.g., 'PL 2630/2020') or bills explicitly containing a keyword in their summary.
    If you want a broad, cross-cutting report about a public policy area (including speeches and hearings), use searchCongressionalThemes instead.

    Args:
        keyword: Keyword to search in the bill's title, subject, or summary (e.g., 'inteligência artificial').
        year: Year of the legislative proposal (e.g., 2024).
        limit: Maximum number of proposals to return (default: 10).
    """
    params = {"palavraChave": keyword}
    if year:
        params["ano"] = str(year)

    data = await make_request(f"{API_BASE_URL}/materia/pesquisa/lista", params=params) or {}
    materias = data.get("PesquisaBasicaMateria", {}).get("Materias", {}).get("Materia", [])
    if isinstance(materias, dict):
        materias = [materias]

    results = [
        {
            "codigo": m.get("Codigo"),
            "identificacao": m.get("DescricaoIdentificacao"),
            "autor": m.get("Autor"),
            "ementa": m.get("Ementa"),
        }
        for m in materias[:limit]
    ]
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
async def getBillText(number: str, year: str) -> str:
    """
    Get the details and text of a legislative bill from the Brazilian Senate.

    Args:
        number: The number of the bill (e.g., '2630').
        year: The year of the bill (e.g., '2020').
    """
    process_data = await make_request(f"{API_BASE_URL}/processo.json?numero={number}&ano={year}")
    if not process_data:
        return json.dumps({"error": f"Bill {number}/{year} not found."})

    primary = process_data[0] if isinstance(process_data, list) else process_data
    doc_url = _find_document_url(primary)
    pdf_bytes = await fetch_document_content(doc_url) if doc_url else None
    text = extract_text_from_pdf(pdf_bytes) if pdf_bytes else ""

    result = {
        "identificacao": primary.get("identificacao", f"{number}/{year}"),
        "ementa": primary.get("ementa"),
        "autoria": primary.get("autoria"),
        "situacao": primary.get("situacaoAtual"),
        "url": doc_url,
        "texto": text,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def getParliamentarianExpenses(
    name: str,
    house: str = "auto",
    year: int | None = None,
    month: int | None = None,
    limit: int = 15,
) -> str:
    """
    Get itemized and aggregated CEAP expenses for a Brazilian parliamentarian.

    Args:
        name: Name of the parliamentarian (e.g., 'Arthur Lira', 'Flávio Bolsonaro').
        house: Legislative house to search. Options: 'auto', 'senado', or 'camara'. Default: 'auto'.
        year: Reference calendar year (e.g., 2024). Defaults to the latest available year.
        month: Reference month (1 to 12).
        limit: Maximum number of itemized expense receipts to return (default: 15).
    """
    result = await get_parliamentarian_expenses(name, house=house, year=year, month=month, limit=limit)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def getParliamentarianActivity(
    name: str,
    house: str = "auto",
    startDate: str | None = None,
    endDate: str | None = None,
    limit: int = 20,
) -> str:
    """
    Get parliamentary attendance, plenary sessions, committee hearings, or speeches.

    Args:
        name: Name of the parliamentarian.
        house: Legislative house to search. Options: 'auto', 'senado', or 'camara'. Default: 'auto'.
        startDate: Filter starting date in YYYY-MM-DD format.
        endDate: Filter ending date in YYYY-MM-DD format.
        limit: Maximum number of events or speeches to return (default: 20).
    """
    result = await get_parliamentarian_activity(
        name, house=house, start_date=startDate, end_date=endDate, limit=limit
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def getParliamentarianVotes(
    name: str,
    house: str = "auto",
    limit: int = 15,
    year: int | None = None,
) -> str:
    """
    Get nominal roll-call voting records and positions for a Brazilian parliamentarian.

    Args:
        name: Name of the parliamentarian.
        house: Legislative house to search. Options: 'auto', 'senado', or 'camara'. Default: 'auto'.
        limit: Maximum number of voting records to return (default: 15).
        year: Year filter for voting records.
    """
    result = await get_parliamentarian_votes(name, house=house, limit=limit, year=year)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def evaluateParliamentarian(
    name: str,
    house: str = "auto",
    year: int | None = None,
) -> str:
    """
    Consolidate biographical data, CEAP expenses, institutional activity, and voting records into a qualitative evaluation.

    Args:
        name: Name of the parliamentarian.
        house: Legislative house to search. Options: 'auto', 'senado', or 'camara'. Default: 'auto'.
        year: Reference year for expenditure and activity analysis.
    """
    result = await evaluate_parliamentarian(name, house=house, year=year)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def searchCongressionalThemes(
    theme: str,
    house: str = "auto",
    limit: int = 5,
    year: int | None = None,
) -> str:
    """
    Synthesize congressional activity surrounding a broad public policy topic (e.g., Sovereignty, Economy).

    Consolidates recent legislative bills, plenary speeches, public hearings,
    committee agendas, and roll-call votes into a cross-cutting thematic report.
    Use this tool to get a macro-level diagnostic of how Congress is handling a specific agenda.
    If you only need to find a specific bill, use searchLegislativeProposals instead.

    Args:
        theme: Public policy topic or keyword (e.g., 'inteligência artificial', 'reforma tributária', 'transição energética').
        house: Legislative house to search. Options: 'auto', 'senado', or 'camara'. Default: 'auto'.
        limit: Maximum number of records per category (default: 5).
        year: Year filter for legislative proposals.
    """
    result = await search_congressional_themes(theme, house=house, limit=limit, year=year)
    return json.dumps(result, ensure_ascii=False, indent=2)


def main():
    try:
        print("Iniciando servidor MCP...", file=sys.stderr)
        mcp.run(transport="stdio")
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()