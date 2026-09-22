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
    participacao = votes.get("participacao_plenario") or {}
    total_plen = participacao.get("votacoes_nominais") or 0
    if total_plen:
        voto_resumo = ", ".join([f"{pos}: {cnt}" for pos, cnt in dist_votos.items()])
        atuacao_votos = (
            f"Em {votes.get('ano_referencia')}, votou em {participacao.get('votou', 0)} de {total_plen} "
            f"votações nominais do Plenário ({participacao.get('taxa_participacao', 0):.1%}). "
            f"Distribuição dos votos registrados: {voto_resumo}."
        )
        if "ausencia_nao_justificada" in participacao:
            atuacao_votos += (
                f" Ausências justificadas: {participacao.get('ausencia_justificada', 0)}; "
                f"não justificadas: {participacao.get('ausencia_nao_justificada', 0)}."
            )
    elif votes.get("total_votacoes_encontradas"):
        voto_resumo = ", ".join([f"{pos}: {cnt}" for pos, cnt in dist_votos.items()])
        atuacao_votos = (
            f"Registrou {votes['total_votacoes_encontradas']} votos nominais. Distribuição: {voto_resumo}."
        )
    else:
        atuacao_votos = "Sem registros de votações nominais no período consultado."

    return {
        "foco_tematico": foco_legislativo,
        "instrucoes_de_analise_mpp": MPP_INSTRUCTION_FRAMEWORK,
        "textos_para_analise": textos_proposicoes,
        "perfil_fiscal_e_transparencia": fiscal_summary,
        "engajamento_plenario": atuacao_votos,
    }


async def evaluate_parliamentarian(
    name: str | None = None,
    house: str = "auto",
    year: int | None = None,
    profile_getter: Any = None,
    parliamentarian_id: int | None = None,
) -> dict[str, Any]:
    """
    Consolidates profile, expenditures, attendance, and roll-call votes into a qualitative evaluation dossier.
    """
    # 1. Resolve once and fetch the profile; everything below is keyed by the official id,
    # so a homonym can never leak into the expenses or votes of the dossier.
    if profile_getter:
        profile_res = await profile_getter(name, house=house, includeVotes=True, limit=10)
    else:
        from .mcp_server import get_profile
        profile_res = await get_profile(
            name, house=house, include_votes=False, limit=10, parliamentarian_id=parliamentarian_id
        )

    if not profile_res or "error" in profile_res:
        return profile_res or {"error": f"Parliamentarian '{name}' not found for evaluation."}

    detected_house = "senado" if profile_res.get("casa") == "Senado Federal" else "camara"
    parl_id = profile_res.get("codigo") or parliamentarian_id
    start_date, end_date = (f"{year}-01-01", f"{year}-12-31") if year else (None, None)

    # 2. Concurrently fetch Expenses, Activity, and Votes
    expenses_res, activity_res, votes_res = await asyncio.gather(
        get_parliamentarian_expenses(
            name, house=detected_house, year=year, limit=10, parliamentarian_id=parl_id
        ),
        get_parliamentarian_activity(
            name, house=detected_house, start_date=start_date, end_date=end_date, limit=15,
            parliamentarian_id=parl_id,
        ),
        get_parliamentarian_votes(
            name, house=detected_house, limit=15, year=year, parliamentarian_id=parl_id
        ),
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
        "id": parl_id,
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
            "quantidade_despesas": expenses_res.get("quantidade_despesas", 0),
            "gastos_por_categoria": expenses_res.get("gastos_por_categoria", {}),
            "principais_fornecedores": expenses_res.get("principais_fornecedores", []),
            "despesas_sem_comprovante_digital": expenses_res.get("despesas_sem_comprovante_digital"),
        },
        "resumo_atividade": {
            "total_eventos_ou_discursos": activity_res.get("total_eventos")
            or activity_res.get("total_discursos", 0),
            "comissoes_ativas": len(profile_res.get("comissoes", [])),
            "proposicoes_autoria": len(profile_res.get("proposicoes_autoria", [])),
        },
        "resumo_votacoes": {
            "ano_referencia": votes_res.get("ano_referencia"),
            "total_votacoes": votes_res.get("total_votacoes_encontradas", 0),
            "distribuicao_votos": votes_res.get("distribuicao_votos", {}),
            "participacao_plenario": votes_res.get("participacao_plenario"),
        },
        "avaliacao_qualitativa": qualitative,
        # Provenance of every figure above, so any claim in the evaluation can be audited.
        "metadados": {
            "despesas": expenses_res.get("metadados") or {"erro": expenses_res.get("error")},
            "atividade": activity_res.get("metadados") or {"erro": activity_res.get("error")},
            "votacoes": votes_res.get("metadados") or {"erro": votes_res.get("error")},
        },
    }
