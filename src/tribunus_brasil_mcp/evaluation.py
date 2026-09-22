import asyncio
from typing import Any
from .transparency import get_parliamentarian_expenses
from .activity import get_parliamentarian_activity, get_parliamentarian_votes


# The MPP rubric is provided to the LLM as instructions for evaluation
MPP_INSTRUCTION_FRAMEWORK = """
Utilize a seguinte 'Matriz de Postura Parlamentar' (MPP) para diagnosticar qualitativamente a atuação do parlamentar, baseando-se nos projetos de lei apresentados, comissões integradas e despesas realizadas:
1. Eixo Geopolítico:
   - Autonomista/Soberanista: Protege a economia nacional, infraestrutura, indústria local, repudia sanções e interferências estrangeiras.
   - Alinhamento Automático/Subserviente: Demonstra dependência, importação acrítica de agendas externas, subserviência diplomática, homenagem a líderes estrangeiros por pautas alienígenas.
2. Eixo Econômico e de Classe:
   - Desenvolvimentista/Pró-Trabalhador: Defesa do Estado como indutor da economia, direitos trabalhistas, fomento à ciência nacional.
   - Rentista/Pró-Lobby Corporativo: Votos favoráveis à financeirização, privatização sem contrapartida, enfraquecimento do Estado (associado a lobbies).
3. Eixo Institucional e Democrático:
   - Garantista/Constitucionalista: Defesa das instituições, direitos humanos, transparência.
   - Punitivista/Autoritário: Erosão institucional, populismo penal, lawfare.
4. Eixo Socioambiental:
   - Sustentabilidade/Direitos Sociais: Defesa do meio ambiente, clima, povos originários, inclusão e diversidade.
   - Extrativismo/Conservadorismo: Fomento a garimpo, agronegócio predatório, flexibilização ambiental e pautas de costumes restritivas.

Instrução: Analise os dados (projetos, gastos, votações) e enquadre o parlamentar nestes eixos de forma nuançada. Não se limite a classificações binárias, observe convergências (ex: altos gastos de consultoria alinhados a lobby corporativo).
"""



def _extract_thematic_keywords(texts: list[str]) -> list[str]:
    """Identify key public policy themes from bill summaries and committee names."""
    themes_map = {
        "Segurança Pública e Defesa": ["segurança", "penal", "crime", "polícia", "armas", "defesa", "militar"],
        "Economia, Tributação e Orçamento": ["tribut", "imposto", "fiscal", "orçamento", "finanças", "crédito", "receita", "econôm"],
        "Relações Exteriores e Comércio": ["exterior", "internacional", "embaixada", "acordo", "guiana", "israel", "arábia", "coreia"],
        "Saúde e Previdência": ["saúde", "sus", "previdência", "médic", "hospital", "doença", "vacina"],
        "Educação, Cultura e Esporte": ["educação", "ensino", "escola", "cultura", "esporte", "universidade"],
        "Meio Ambiente e Sustentabilidade": ["ambiente", "sustent", "clima", "floresta", "mineração", "barragem", "água"],
        "Administração Pública e Transparência": ["administração", "servidor", "transparência", "corrupção", "processo", "cpi"],
        "Infraestrutura e Transportes": ["transporte", "rodovia", "ferrovia", "porto", "aviação", "energia", "telecom"],
    }

    aggregated_text = " ".join(texts).lower()
    matched_themes: list[tuple[str, int]] = []

    for theme, keywords in themes_map.items():
        score = sum(aggregated_text.count(kw) for kw in keywords)
        if score > 0:
            matched_themes.append((theme, score))

    matched_themes.sort(key=lambda x: x[1], reverse=True)
    return [t[0] for t in matched_themes[:4]] or ["Atuação Legislativa Geral"]


def _generate_qualitative_assessment(
    profile: dict[str, Any],
    expenses: dict[str, Any],
    activity: dict[str, Any],
    votes: dict[str, Any],
    themes: list[str],
) -> dict[str, Any]:
    """Synthesize qualitative evaluation sections based on factual congressional data."""
    parl_name = profile.get("parlamentar", "Parlamentar")
    casa = profile.get("casa", "Congresso Nacional")
    partido = profile.get("partido", "Sem partido")
    uf = profile.get("uf", "BR")

    comissoes = profile.get("comissoes", [])
    proposicoes = profile.get("proposicoes_autoria", [])
    total_despesas = expenses.get("total_despesas", 0.0)
    gastos_cat = expenses.get("gastos_por_categoria", {})
    top_suppliers = expenses.get("principais_fornecedores", [])
    ano_ref = expenses.get("ano_referencia")

    # Assess legislative focus
    foco_legislativo = (
        f"O parlamentar {parl_name} ({partido}/{uf}) concentra sua atuação institucional nas áreas de "
        f"{', '.join(themes)}. "
        f"Registra {len(comissoes)} comissões ou colegiados e {len(proposicoes)} proposições de autoria catalogadas recentemente."
    )

    # The LLM interprets the raw texts using the MPP framework
    textos_proposicoes = [p.get("ementa", "") for p in proposicoes]
    
    # Assess fiscal spending profile and cross-reference with lobby potential
    if total_despesas > 0:
        top_cats = list(gastos_cat.items())[:3]
        cat_desc = ", ".join([f"{k} (R$ {v:,.2f})" for k, v in top_cats])
        fiscal_summary = (
            f"No ano-base de {ano_ref}, o montante registrado na cota parlamentar totaliza "
            f"R$ {total_despesas:,.2f}. As principais rubricas concentram-se em: {cat_desc}. "
        )
        if top_suppliers:
            sup_names = [s["fornecedor"] for s in top_suppliers[:2]]
            fiscal_summary += f"Maiores fornecedores (indicadores de transparência e foco logístico): {', '.join(sup_names)}."
    else:
        fiscal_summary = "Não foram identificadas despesas liquidadas recentes, indicando uso reduzido da cota ou atraso nos registros."

    # Assess institutional activity & voting
    dist_votos = votes.get("distribuicao_votos", {})
    total_votos = votes.get("total_votacoes_encontradas", 0)
    if total_votos > 0:
        voto_resumo = ", ".join([f"{pos}: {cnt}" for pos, cnt in dist_votos.items()])
        atuacao_votos = (
            f"Registrou participação em {total_votos} votações nominais examinadas. Distribuição: {voto_resumo}."
        )
    else:
        atuacao_votos = "Sem registros detalhados de votações nominais no intervalo amostrado."

    return {
        "foco_tematico": foco_legislativo,
        "instrucoes_de_analise_mpp": MPP_INSTRUCTION_FRAMEWORK,
        "textos_para_analise": textos_proposicoes,
        "perfil_fiscal_e_transparencia": fiscal_summary,
        "engajamento_plenario": atuacao_votos,
    }


async def evaluate_parliamentarian(
    name: str,
    house: str = "auto",
    year: int | None = None,
    profile_getter: Any = None,
) -> dict[str, Any]:
    """
    Consolidates profile, expenditures, attendance, and roll-call votes into a qualitative evaluation dossier.
    """
    # 1. Fetch Profile
    if profile_getter:
        profile_res = await profile_getter(name, house=house, includeVotes=True, limit=10)
    else:
        from .mcp_server import _get_senator, _get_deputy
        target_house = house.lower()
        profile_res = None
        if target_house in ("auto", "senado"):
            profile_res = await _get_senator(name, include_votes=True, limit=10)
        if not profile_res and target_house in ("auto", "camara"):
            profile_res = await _get_deputy(name, include_votes=True, limit=10)

    if not profile_res or "error" in profile_res:
        return {"error": f"Parliamentarian '{name}' not found for evaluation."}

    detected_house = "senado" if profile_res.get("casa") == "Senado Federal" else "camara"

    # 2. Concurrently fetch Expenses, Activity, and Votes
    expenses_task = asyncio.create_task(
        get_parliamentarian_expenses(name, house=detected_house, year=year, limit=10)
    )
    activity_task = asyncio.create_task(
        get_parliamentarian_activity(name, house=detected_house, limit=15)
    )
    votes_task = asyncio.create_task(
        get_parliamentarian_votes(name, house=detected_house, limit=15, year=year)
    )

    expenses_res, activity_res, votes_res = await asyncio.gather(
        expenses_task, activity_task, votes_task
    )

    # 3. Extract Themes
    bill_texts = [
        p.get("ementa", "") for p in profile_res.get("proposicoes_autoria", [])
    ]
    committee_texts = [
        c.get("nome", "") for c in profile_res.get("comissoes", [])
    ]
    all_texts = bill_texts + committee_texts
    themes = _extract_thematic_keywords(all_texts)

    # 4. Generate Qualitative Synthesis
    qualitative = _generate_qualitative_assessment(
        profile_res, expenses_res, activity_res, votes_res, themes
    )

    return {
        "parlamentar": profile_res.get("parlamentar"),
        "nome_civil": profile_res.get("nome_civil"),
        "casa": profile_res.get("casa"),
        "partido": profile_res.get("partido"),
        "uf": profile_res.get("uf"),
        "email": profile_res.get("email"),
        "foto": profile_res.get("foto"),
        "temas_predominantes": themes,
        "resumo_despesas": {
            "ano_referencia": expenses_res.get("ano_referencia"),
            "total_despesas": expenses_res.get("total_despesas", 0.0),
            "gastos_por_categoria": expenses_res.get("gastos_por_categoria", {}),
            "principais_fornecedores": expenses_res.get("principais_fornecedores", []),
        },
        "resumo_atividade": {
            "total_eventos_ou_discursos": activity_res.get("total_eventos")
            or activity_res.get("total_discursos", 0),
            "comissoes_ativas": len(profile_res.get("comissoes", [])),
            "proposicoes_autoria": len(profile_res.get("proposicoes_autoria", [])),
        },
        "resumo_votacoes": {
            "total_votacoes": votes_res.get("total_votacoes_encontradas", 0),
            "distribuicao_votos": votes_res.get("distribuicao_votos", {}),
        },
        "avaliacao_qualitativa": qualitative,
    }
