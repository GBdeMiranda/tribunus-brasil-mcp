"""
Investigative roadmap of a mandate ("roteiro do parlamentar") from the standpoint of working
people - whom the mandate serves in practice - returned with the analytical instruction for the
calling LLM:

- votacoes_por_eixo   - every merit vote, classified into thematic evaluation axes by official themes
- base_social         - organized interests the mandate joins (parliamentary fronts, committees)
- fundo_publico       - where the mandate sends public money (individual amendments)
- publicidade         - what a citizen can verify about the mandate
- agenda_e_discurso   - what the mandate proposes and says
"""

import asyncio
import collections
import datetime
import re
from typing import Any
from .votes import legislatura, votes_by_axis
from .emendas import PRIVATE_GROUPS, RECIPIENT_GROUPS, TIPO_TRANSFERENCIA_ESPECIAL, get_amendments
from .identity import HOUSE_LABELS, resolve_parliamentarian
from .publicity import audit_publicity
from .utils import fetch_all_pages, make_request, normalize_name, ratio

CAMARA_BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
SENADO_BASE_URL = "https://legis.senado.leg.br/dadosabertos"

# Normative reference, criteria for reading each bill, evaluation axes and principles for the
# mandate's own spending. Sources for each criterion: docs/methodology.md.
QUADRO_ANALITICO = """
Referência normativa: a Constituição funda a República nos valores sociais do trabalho (art. 1º, IV), tem como objetivos erradicar a pobreza e reduzir as desigualdades (art. 3º), garante os direitos sociais e do trabalho, a organização sindical e a greve (arts. 6º a 9º), funda a ordem econômica na valorização do trabalho humano (art. 170) e dá à ordem social a base do primado do trabalho e o objetivo do bem-estar e da justiça sociais (art. 193). Os votos são lidos contra essa referência.

Leitura de cada matéria (aplique a todas as matérias em destaque, do mesmo modo para qualquer partido ou governo):
a) Quem ganha e quem paga - identifique quem recebe o benefício e quem arca com o custo (trabalhadores, consumidores, contribuintes, frações do capital, cúpula do Estado, a própria classe política). Benefício concentrado em um setor com custo diluído entre muitos é o padrão da matéria de interesse de grupo organizado.
b) Capital e trabalho - nas matérias trabalhistas, veja o efeito sobre formas de contratação, jornada, remuneração, saúde e segurança no trabalho, organização sindical e negociação coletiva, acesso à Justiça do Trabalho e fiscalização. A matéria desloca poder para o empregador ou para o trabalhador?
c) Dependência do mercado - a matéria amplia ou reduz a capacidade de viver com dignidade sem depender só da venda da força de trabalho (previdência, assistência, saúde, educação, moradia e transporte públicos e universais)? Converter direito em serviço pago, seguro privado ou crédito é mercantilização.
d) Fundo público - para onde a matéria move o orçamento: reprodução da vida de quem trabalha (seguridade social, serviços, investimento público) ou remuneração do capital (juros da dívida, renúncias, subsídios, garantias a investidores)? Regras fiscais que limitam o gasto social e preservam o serviço da dívida entram aqui. Na tributação: quem paga mais, a renda e o patrimônio do topo ou o consumo e o salário?
e) Fração do capital - quando a matéria favorece o capital, diga qual fração (financeira e rentista, agronegócio exportador, mineração e extrativismo, indústria e construção nacionais, capital estrangeiro e transnacional) e se ela gera emprego, renda e capacidade produtiva no país ou aprofunda a especialização primário-exportadora e a dependência externa. Nos tratados e acordos internacionais: quem ganha acesso a mercado, quem perde emprego e padrão de proteção.
f) Retrocesso encoberto - corte por mudança técnica (regra de cálculo, índice de reajuste, regra de transição, desvinculação de receita, prazo, carência) e a falta de atualização do que perde valor com o tempo (tabela do imposto de renda, valores de benefícios, pisos) têm o mesmo efeito de um corte aberto.
g) Promessa e prova - benefício ao trabalhador alegado como efeito indireto (emprego prometido por desoneração, renúncia, subsídio, privatização ou flexibilização) só conta com contrapartida verificável no texto aprovado; o mesmo vale para o gasto direto.
h) Autobenefício e clientela - matérias em que o Parlamento legisla em causa própria (fundo eleitoral e partidário, anistia a partidos e candidatos, proteção de parlamentares contra investigação, emendas sem rastro, remuneração e benefícios de agentes políticos) ou da cúpula do Estado (pagamentos acima do teto, regimes especiais de carreira e aposentadoria), e o uso do orçamento no reduto em troca de apoio. Separe a cúpula do conjunto dos servidores. Julgue a emenda pelo critério de destinação (necessidade, política pública universal), e não pelo volume: emenda a serviço público de município carente é política pública; emenda a entidade ligada ao mandato, concentrada no reduto e paga junto com votos de apoio ao governo é pista de clientela.

Eixos (as matérias de votacoes_por_eixo já vêm classificadas neles pelos temas oficiais das Casas):
1. Trabalho, Renda, Previdência e Tributação (trabalho_renda_e_previdencia):
   - Proteção do trabalho e da renda: salário mínimo e poder de compra, direitos trabalhistas e previdenciários, negociação coletiva e organização sindical, tributação progressiva sobre a renda e o patrimônio do topo.
   - Precarização e regressividade: contratação precária, jornada flexibilizada, rebaixamento da remuneração, enfraquecimento sindical e da Justiça do Trabalho, cortes previdenciários, tributação sobre consumo e salário, renúncias a grupos econômicos sem contrapartida.
2. Serviços Públicos, Direitos Sociais e Meio Ambiente (servicos_publicos_e_bem_estar):
   - Universalização: SUS, educação pública, assistência social, moradia, saneamento, transporte coletivo, segurança alimentar e agricultura familiar, terra e território de povos e comunidades, proteção ambiental de quem vive e trabalha no território.
   - Mercantilização e desmonte: subfinanciamento, entrega de serviço público a operador privado com lucro garantido, cobrança pelo acesso, flexibilização ambiental e fundiária que transfere terra, água e renda para o capital e o dano para as comunidades.
3. Patrimônio Público, Infraestrutura e Soberania Nacional (patrimonio_publico_e_soberania):
   - Desenvolvimento nacional: controle público de energia, infraestrutura, recursos naturais e empresas estratégicas; política industrial e científica que gera emprego qualificado no país; tarifas módicas.
   - Alienação e dependência: venda de estatais e de monopólios naturais, concessões sem controle de tarifa e investimento, abertura que destrói emprego nacional, alinhamento externo que restringe a política econômica do país.
4. Administração Pública, Justiça e Garantias Constitucionais (institucional_e_administracao_publica):
   - Garantias e serviço público: liberdades de organização, manifestação e greve; garantias processuais; serviço público com carreira e concurso; segurança pública que protege os bairros onde vive quem trabalha.
   - Repressão e desmonte: criminalização de movimentos sociais, sindicatos e greves; expansão penal seletiva sobre os pobres; desmonte da fiscalização do trabalho, ambiental e tributária; privilégios da cúpula do Estado (supersalários, regimes especiais), distintos dos direitos do conjunto dos servidores.
5. Transparência, Moralidade e Controle Social (transparencia_e_controle_social):
   - Controle social: publicidade de gastos, votos e emendas, rastreabilidade do orçamento, acesso à informação.
   - Opacidade e blindagem: sigilo, emendas sem rastro, votação secreta, proteção de parlamentares contra investigação e responsabilização.

Uso do dinheiro público pelo mandato (cota, gabinete, emendas), pelos princípios da administração pública (CF, art. 37):
- Legalidade: regularidade cadastral e fiscal de fornecedores e favorecidos.
- Impessoalidade: finalidade pública acima de promoção pessoal, reduto eleitoral ou entidade ligada ao mandato.
- Moralidade: conflito de interesses entre frentes, fornecedores e destinatários de emendas.
- Publicidade: rastreabilidade de comprovantes, fornecedores e destino final das emendas.
- Eficiência: finalidade e qualidade do gasto - a quem serve e para quê (serviços públicos universais, municípios com maior carência), e não o volume.
"""

INSTRUCAO = """Com base nos dados abaixo, elabore o roteiro investigativo do mandato de {nome} ({casa}, {partido}/{uf}): a quem o mandato serve na prática, aos trabalhadores ou a frações do capital.
{diretrizes}
Como conduzir:
1. Votações por eixo - em cada eixo, leia as matérias em destaque (detalhadas em votacoes_por_eixo.materias: ementa, voto, placar, maioria do partido, orientação do governo), aplique a leitura (a-h) e classifique o efeito do voto do parlamentar para os trabalhadores: favorável, desfavorável ou sem efeito claro, dizendo por quê. Pese mais as votações disputadas, as divergências em relação ao partido e as ausências em votações apertadas; matérias consensuais dizem pouco sobre o parlamentar.
2. Posicionamento - compare o comportamento sob cada governo: o parlamentar vota pelo conteúdo ou pela posição em relação ao governo? O voto em plenário segue em regra a orientação do líder; o que revela o parlamentar é a divergência. Votar contra o consenso da Casa e contra o governo em matérias de conteúdo semelhante ao que apoiou antes indica oportunismo; coerência de conteúdo sob governos diferentes indica o contrário. Acompanhar governos de orientação oposta em matérias de conteúdo oposto indica atuação fisiológica.
3. Interesses organizados - cruze frentes parlamentares, comissões, proposições, votos e destino das emendas: quais setores e grupos econômicos ganham com a atuação do mandato? A posição pública de entidades patronais (CNI, CNA, CNC, Febraban) e de trabalhadores (DIAP, centrais sindicais) sobre as matérias votadas é verificação externa útil.
4. Fundo público - aplique os princípios do art. 37 ao uso do dinheiro pelo mandato: para quem vai, para quê, em que função, com que rastreabilidade (transferências especiais, favorecidos não identificados, associações e empresas), concentração no território e na cidade natal. O ritmo de pagamento das emendas acompanhar o apoio ao governo é pista de troca de emendas por votos.
5. Publicidade - informa o eixo de Transparência: o que o cidadão consegue verificar sobre a cota parlamentar, os fornecedores (situação na Receita Federal, abertura recente), o gasto com divulgação (em reais e em salários mínimos), as emendas e os votos; separe regra da Casa de escolha do parlamentar. Pouco gasto público com divulgação diante de forte presença pública é gatilho para verificação externa, não conclusão.
6. Discurso e prática - o que o mandato fala e propõe corresponde ao que vota e para onde envia o orçamento? Julgue o conteúdo das proposições (a quem servem), não a quantidade.
7. Pistas - liste o que merece investigação externa, com o fato que a motiva e onde verificar.

Conclusão: caracterize com clareza, quando os dados sustentarem, a quem o mandato serve na prática (aos trabalhadores, a quais frações do capital, à cúpula do Estado e à própria classe política) e por qual lógica atua (programática, fisiológica, clientelista ou corporativa), incluindo o que o mandato faz de bom para os trabalhadores.

Critérios:
- O critério é o efeito material sobre a vida de quem vive do trabalho (renda, emprego, jornada, direitos, previdência, custo de vida, serviços públicos), aplicado da mesma forma a qualquer partido ou governo.
- Presença, número de proposições e valores gastos são contexto; o juízo recai sobre conteúdo, beneficiários e coerência.
- Apoie cada afirmação em dado do bloco (matéria, data, voto, valor) e cite magnitude absoluta ao lado de percentuais.
- O que depende de checagem externa é pista, não conclusão.
- Linguagem descritiva e sóbria.
- Feche com perguntas objetivas ao parlamentar."""

# Parliamentary fronts grouped into organized-interest blocs (patterns match accent-free,
# lower-case titles on word boundaries).
FRENTE_BLOCOS: dict[str, list[str]] = {
    "agronegocio": [r"agropecuari\w*", r"agronegocio", r"produtor\w* rura\w*", r"agricultura(?! familiar)", r"pecuari\w*",
                    r"cooperativismo", r"biocombustive\w*", r"etanol", r"cafe", r"soja", r"algodao", r"florestas plantadas"],
    "mercado_e_empresariado": [r"livre mercado", r"empreendedor\w*", r"ambiente de negocios", r"comercio", r"setor de servicos",
                               r"industria\w*", r"setor produtivo", r"livre iniciativa", r"desburocratiza\w*",
                               r"mineracao", r"petroleo", r"logistica", r"construcao civil", r"franquia\w*", r"turismo",
                               r"investimentos?( estrangeiros?)?", r"economia do mar", r"navegacao", r"nautic\w*",
                               r"eletromobilidade"],
    "setor_financeiro": [r"mercado de capitais", r"seguros?", r"fintech\w*", r"sistema financeiro", r"credito",
                         r"microcredito", r"microfinanc\w*", r"criptoativos?", r"ativos digitais",
                         r"previdencia complementar", r"bancos?"],
    "religiao_e_costumes": [r"evangelica", r"catolica", r"crista\w*", r"familia", r"defesa da vida", r"valores",
                            r"liberdade religiosa"],
    "seguranca_e_armas": [r"seguranca publica", r"armas?", r"legitima defesa", r"policia\w*", r"militar\w*",
                          r"combate ao crime\w*", r"forcas armadas", r"susp", r"ciberseguranca", r"defesa cibernetica"],
    "trabalho_e_servico_publico": [r"trabalhador\w*", r"sindica\w*", r"servidor\w*", r"servico publico", r"trabalho",
                                   r"emprego", r"aposentad\w*", r"salario minimo", r"economia solidaria"],
    "direitos_e_povos": [r"direitos humanos", r"indigena\w*", r"quilombola\w*", r"lgbt\w*", r"mulher\w*",
                         r"igualdade racial", r"populacao negra", r"pessoas? com deficiencia", r"crianca\w*",
                         r"idos[oa]s?", r"juventude", r"reforma agraria", r"agricultura familiar"],
    "ambiente_e_clima": [r"ambientalista", r"clima\w*", r"sustentav\w*", r"meio ambiente", r"amazonia", r"agua",
                         r"residuos"],
    "saude_e_educacao_publicas": [r"sus", r"saude publica", r"servicos de saude", r"educacao publica", r"universidade\w*",
                                  r"escola publica", r"institutos federais", r"ciencia", r"pesquisa biomedica", r"enfermagem"],
    "transparencia_e_controle": [r"transparencia", r"combate a corrupcao", r"controle social", r"acesso a informacao",
                                 r"etica na politica"],
    "soberania_e_estatais": [r"soberania", r"petrobras", r"estata\w*", r"empresas publicas", r"correios",
                             r"banco do brasil", r"caixa economica", r"bndes"],
}
_FRENTE_PATTERNS = {
    bloc: re.compile(r"\b(?:" + "|".join(patterns) + r")\b") for bloc, patterns in FRENTE_BLOCOS.items()
}

SUBSTANTIVE_BILLS_CAMARA = "PL,PLP,PEC,PDL"
SUBSTANTIVE_BILLS_SENADO = {"PL", "PLS", "PLP", "PEC", "PDL", "PDS"}
RECENT_ITEMS = 15
RECENT_BILLS = 40


def _legislature_start(today: datetime.date) -> datetime.date:
    return datetime.date(2015 + (legislatura(today.isoformat()) - 55) * 4, 2, 1)


def _is_local_government(natureza: str | None) -> bool:
    n = normalize_name(natureza or "")
    return n.startswith(("municipio", "estado ou distrito federal")) or "municipal" in n or "estadual" in n


def classify_front(title: str) -> list[str]:
    normalized = normalize_name(title)
    return [bloc for bloc, pattern in _FRENTE_PATTERNS.items() if pattern.search(normalized)]


async def _social_base(parl: dict[str, Any], today: datetime.date) -> dict[str, Any]:
    if parl["casa"] == "camara":
        frentes_data, orgaos = await asyncio.gather(
            make_request(f"{CAMARA_BASE_URL}/deputados/{parl['id']}/frentes"),
            fetch_all_pages(f"{CAMARA_BASE_URL}/deputados/{parl['id']}/orgaos", params={"itens": 100}),
        )
        current_leg = legislatura(today.isoformat())
        frentes = [f for f in (frentes_data or {}).get("dados", []) if f.get("idLegislatura") == current_leg]
        blocs: dict[str, list[str]] = collections.defaultdict(list)
        for f in frentes:
            for bloc in classify_front(f.get("titulo") or ""):
                blocs[bloc].append(f["titulo"])
        comissoes = [
            {"sigla": o.get("siglaOrgao"), "nome": o.get("nomeOrgao"), "cargo": o.get("titulo")}
            for o in orgaos[0]
            if not o.get("dataFim") or o["dataFim"][:10] >= today.isoformat()
        ]
        return {
            "frentes_parlamentares": {
                "total": len(frentes),
                "por_bloco": {
                    bloc: {"quantidade": len(titles), "frentes": titles}
                    for bloc, titles in sorted(blocs.items(), key=lambda kv: -len(kv[1]))
                },
                "sem_bloco": [f["titulo"] for f in frentes if not classify_front(f.get("titulo") or "")],
            },
            "comissoes_atuais": comissoes,
        }

    data = await make_request(f"{SENADO_BASE_URL}/senador/{parl['id']}/comissoes") or {}
    comm = data.get("MembroComissaoParlamentar", {}).get("Parlamentar", {}).get("MembroComissoes", {}).get("Comissao", [])
    comm = [comm] if isinstance(comm, dict) else comm
    current = [c for c in comm if not c.get("DataFim") or c["DataFim"][:10] >= today.isoformat()]

    # The Senate lists parliamentary fronts and bilateral groups among its committees.
    frentes, grupos, comissoes = [], [], []
    for c in current:
        ident = c.get("IdentificacaoComissao", {})
        nome = ident.get("NomeComissao") or ""
        if normalize_name(nome).startswith("frente parlamentar"):
            frentes.append(nome)
        elif normalize_name(nome).startswith("grupo parlamentar"):
            grupos.append(nome)
        else:
            comissoes.append({"sigla": ident.get("SiglaComissao"), "nome": nome, "cargo": c.get("DescricaoParticipacao")})

    blocs: dict[str, list[str]] = collections.defaultdict(list)
    for title in frentes:
        for bloc in classify_front(title):
            blocs[bloc].append(title)
    return {
        "frentes_parlamentares": {
            "total": len(frentes),
            "por_bloco": {
                bloc: {"quantidade": len(titles), "frentes": titles}
                for bloc, titles in sorted(blocs.items(), key=lambda kv: -len(kv[1]))
            },
            "sem_bloco": [t for t in frentes if not classify_front(t)],
        },
        "grupos_parlamentares_bilaterais": grupos,
        "comissoes_atuais": comissoes,
    }


async def _birthplace(parl: dict[str, Any]) -> str | None:
    if parl["casa"] == "camara":
        d = ((await make_request(f"{CAMARA_BASE_URL}/deputados/{parl['id']}")) or {}).get("dados") or {}
        city, uf = d.get("municipioNascimento"), d.get("ufNascimento")
    else:
        data = await make_request(f"{SENADO_BASE_URL}/senador/{parl['id']}") or {}
        d = data.get("DetalheParlamentar", {}).get("Parlamentar", {}).get("DadosBasicosParlamentar", {})
        city, uf = d.get("Naturalidade"), d.get("UfNaturalidade")
    return f"{city}/{uf}" if city else None


async def public_fund(parl: dict[str, Any], years: list[int]) -> dict[str, Any]:
    """Individual amendments of `years`: area (função), recipients and territorial concentration."""
    data = await get_amendments([parl["nome"], parl.get("nome_civil")], years)
    if not data.get("localizado"):
        return {
            "localizado": False,
            "motivo": data.get("erro") or "Autor não localizado no Portal da Transparência pelo nome parlamentar ou civil.",
        }

    individual = [e for e in data["emendas"] if (e["tipo"] or "").startswith("Emenda Individual")]
    committed = sum(e["empenhado"] for e in individual)
    per_year = {}
    for year in years:
        rows = [e for e in individual if e["ano"] == year]
        if rows:
            special = [e for e in rows if e["tipo"] == TIPO_TRANSFERENCIA_ESPECIAL]
            per_year[year] = {
                "emendas": len(rows),
                "empenhado": round(sum(e["empenhado"] for e in rows), 2),
                "pago": round(sum(e["pago"] for e in rows), 2),
                "transferencias_especiais_empenhado": round(sum(e["empenhado"] for e in special), 2),
            }

    by_function: dict[str, float] = collections.defaultdict(float)
    for e in individual:
        by_function[e["funcao"] or "Não informada"] += e["empenhado"]

    payments = data["pagamentos"]
    received = sum(p["valor"] for p in payments)
    groups = {}
    for group in RECIPIENT_GROUPS:
        rows = [p for p in payments if p["grupo"] == group]
        value = sum(p["valor"] for p in rows)
        groups[group] = {
            "favorecidos": len({p["documento_favorecido"] or p["favorecido"] for p in rows}),
            "valor": round(value, 2),
            "percentual": ratio(value, received),
        }

    # Territory is read from states and municipalities receiving the money; companies, banks
    # and federal bodies are registered where their headquarters are, not where it is spent.
    local = [p for p in payments if p["grupo"] == "ente_ou_entidade_publica" and _is_local_government(p["natureza_juridica"])]
    local_total = sum(p["valor"] for p in local)
    by_place: dict[tuple[str, str], float] = collections.defaultdict(float)
    for p in local:
        by_place[(p["municipio"] or "?", p["uf"] or "?")] += p["valor"]
    top_places = sorted(by_place.items(), key=lambda kv: -kv[1])[:10]
    outside_uf = sum(p["valor"] for p in local if p["uf"] and parl.get("uf") and p["uf"] != parl["uf"])

    private: dict[str, dict[str, Any]] = {}
    for p in payments:
        if p["grupo"] in PRIVATE_GROUPS:
            e = private.setdefault(p["documento_favorecido"] or p["favorecido"], {
                "favorecido": p["favorecido"], "cnpj": p["documento_favorecido"], "natureza_juridica": p["natureza_juridica"],
                "municipio": p["municipio"], "uf": p["uf"], "valor": 0.0,
            })
            e["valor"] += p["valor"]

    return {
        "localizado": True,
        "emendas_individuais_por_ano": per_year,
        "empenhado_total": round(committed, 2),
        "por_funcao": [
            {"funcao": f, "empenhado": round(v, 2), "percentual": ratio(v, committed)}
            for f, v in sorted(by_function.items(), key=lambda kv: -kv[1])[:8]
        ],
        "destino_dos_pagamentos": {"total_recebido": round(received, 2), "por_tipo_de_favorecido": groups},
        "concentracao_territorial": {
            "recebido_por_estados_e_municipios": round(local_total, 2),
            "localidades_distintas": len(by_place),
            "dez_maiores": [
                {"municipio": m, "uf": uf, "valor": round(v, 2), "percentual": ratio(v, local_total)}
                for (m, uf), v in top_places
            ],
            "percentual_dez_maiores": ratio(sum(v for _, v in top_places), local_total),
            "fora_da_uf_do_mandato": {"valor": round(outside_uf, 2), "percentual": ratio(outside_uf, local_total)},
        },
        "maiores_favorecidos_privados": [
            {**e, "valor": round(e["valor"], 2)} for e in sorted(private.values(), key=lambda x: -x["valor"])[:10]
        ],
        "referencia_autores": data.get("referencia_autores"),
        "fonte": (data.get("metadados") or {}).get("fonte"),
    }


async def _publicity(parl: dict[str, Any], year: int) -> dict[str, Any]:
    result = await audit_publicity(house=parl["casa"], year=year, parliamentarian_id=parl["id"])
    if "error" in result:
        return result
    ind = result["indicadores"]
    # Keep magnitudes and the most telling evidence; the full audit is available via auditPublicity.
    ind["rastreabilidade_fornecedores"].pop("maiores_fornecedores", None)
    ind["transparencia_emendas"].pop("emendas", None)
    ind["transparencia_emendas"].pop("maiores_favorecidos_privados", None)
    for indicator in ind.values():
        indicator.pop("metadados", None)
        if isinstance(indicator.get("evidencias"), list):
            indicator["evidencias"] = indicator["evidencias"][:5]
    return {"ano_referencia": year, **ind, "perguntas_de_accountability": result["perguntas_de_accountability"]}


async def _camara_bills(dep_id: int, years: list[int]) -> list[dict[str, Any]]:
    pages = await asyncio.gather(*(
        fetch_all_pages(
            f"{CAMARA_BASE_URL}/proposicoes",
            params={"idDeputadoAutor": dep_id, "ano": y, "siglaTipo": SUBSTANTIVE_BILLS_CAMARA, "ordem": "DESC", "ordenarPor": "id"},
        )
        for y in years
    ))
    bills = [p for records, _ in pages for p in records]
    return [
        {"identificacao": f"{b.get('siglaTipo')} {b.get('numero')}/{b.get('ano')}", "tipo": b.get("siglaTipo"),
         "ano": b.get("ano"), "ementa": b.get("ementa")}
        for b in sorted(bills, key=lambda b: -(b.get("id") or 0))
    ]


async def _senado_bills(code: int, start: datetime.date) -> list[dict[str, Any]]:
    data = await make_request(f"{SENADO_BASE_URL}/senador/{code}/autorias") or {}
    rows = data.get("MateriasAutoriaParlamentar", {}).get("Parlamentar", {}).get("Autorias", {}).get("Autoria", [])
    rows = [rows] if isinstance(rows, dict) else rows
    bills = []
    for r in rows:
        m = r.get("Materia", {})
        if m.get("Sigla") in SUBSTANTIVE_BILLS_SENADO and (m.get("Data") or "") >= start.isoformat():
            bills.append({"identificacao": m.get("DescricaoIdentificacao"), "tipo": m.get("Sigla"),
                          "ano": int(m.get("Ano") or 0), "ementa": m.get("Ementa"), "data": m.get("Data"),
                          "autor_principal": r.get("IndicadorAutorPrincipal") == "Sim"})
    return sorted(bills, key=lambda b: b.get("data") or "", reverse=True)


async def _camara_speeches(dep_id: int, start: datetime.date, today: datetime.date) -> list[dict[str, Any]]:
    records, _ = await fetch_all_pages(
        f"{CAMARA_BASE_URL}/deputados/{dep_id}/discursos",
        params={"dataInicio": start.isoformat(), "dataFim": today.isoformat(), "ordenarPor": "dataHoraInicio", "ordem": "DESC"},
    )
    return [
        {"data": (s.get("dataHoraInicio") or "")[:10], "tipo": s.get("tipoDiscurso"),
         "palavras_chave": [k.strip() for k in (s.get("keywords") or "").split(",") if k.strip()],
         "resumo": s.get("sumario")}
        for s in records
    ]


async def _senado_speeches(code: int, start: datetime.date, today: datetime.date) -> list[dict[str, Any]]:
    # The endpoint caps the period at about a year, so ask year by year.
    responses = await asyncio.gather(*(
        make_request(
            f"{SENADO_BASE_URL}/senador/{code}/discursos",
            params={"dataInicio": f"{y}0101", "dataFim": f"{y}1231"},
        )
        for y in range(start.year, today.year + 1)
    ))
    speeches = []
    for data in responses:
        rows = (data or {}).get("DiscursosParlamentar", {}).get("Parlamentar", {}).get("Pronunciamentos", {}).get("Pronunciamento", [])
        rows = [rows] if isinstance(rows, dict) else rows
        for s in rows:
            date = s.get("DataPronunciamento") or ""
            if date >= start.isoformat():
                tipo = s.get("TipoUsoPalavra")
                speeches.append({"data": date, "tipo": tipo.get("Descricao") if isinstance(tipo, dict) else None,
                                 "resumo": s.get("TextoResumo")})
    return sorted(speeches, key=lambda s: s["data"], reverse=True)


async def _agenda_and_discourse(parl: dict[str, Any], start: datetime.date, today: datetime.date) -> dict[str, Any]:
    years = list(range(start.year, today.year + 1))
    if parl["casa"] == "camara":
        bills, speeches = await asyncio.gather(
            _camara_bills(parl["id"], years), _camara_speeches(parl["id"], start, today)
        )
    else:
        bills, speeches = await asyncio.gather(
            _senado_bills(parl["id"], start), _senado_speeches(parl["id"], start, today)
        )

    keywords = collections.Counter(k for s in speeches for k in s.get("palavras_chave", []))
    if parl["casa"] == "senado":
        proposicoes = {"como_autor_principal": [b for b in bills if b["autor_principal"]][:RECENT_BILLS]}
    else:
        # The Chamber lists every signatory as author; co-signed bills are included.
        proposicoes = {"assinadas": bills[:RECENT_BILLS]}
    return {
        "proposicoes": proposicoes,
        "discursos": {
            **({"palavras_chave_mais_frequentes": keywords.most_common(20)} if keywords else {}),
            "mais_recentes": [{k: v for k, v in s.items() if k != "palavras_chave"} for s in speeches[:RECENT_ITEMS]],
        },
    }


async def evaluate_parliamentarian(
    name: str | None = None,
    house: str = "auto",
    year: int | None = None,
    parliamentarian_id: int | None = None,
    desde: int | None = None,
) -> dict[str, Any]:
    """Investigative roadmap of a mandate from the standpoint of working people, with its interpretation instruction."""
    parl = await resolve_parliamentarian(name, house=house, parliamentarian_id=parliamentarian_id)
    if "error" in parl:
        return parl

    today = datetime.date.today()
    start = _legislature_start(today)
    vote_years = list(range(min(desde or start.year, today.year), today.year + 1))
    publicity_year = year or today.year - 1
    fund_years = list(range(start.year, today.year + 1))

    naturalidade, votacoes, base, fundo, publicidade, agenda = await asyncio.gather(
        _birthplace(parl),
        votes_by_axis(parl, vote_years),
        _social_base(parl, today),
        public_fund(parl, fund_years),
        _publicity(parl, publicity_year),
        _agenda_and_discourse(parl, start, today),
    )

    return {
        "parlamentar": {
            "nome": parl["nome"],
            "id": parl["id"],
            "casa": HOUSE_LABELS[parl["casa"]],
            "partido": parl.get("partido"),
            "uf": parl.get("uf"),
            "naturalidade": naturalidade,
        },
        "periodo": {
            "votacoes": votacoes["periodo"],
            "legislatura_atual_desde": start.isoformat(),
            "publicidade": publicity_year,
        },
        "dimensoes": {
            "votacoes_por_eixo": votacoes,
            "base_social": base,
            "fundo_publico": fundo,
            "publicidade": publicidade,
            "agenda_e_discurso": agenda,
        },
        "instrucao": INSTRUCAO.format(
            nome=parl["nome"], casa=HOUSE_LABELS[parl["casa"]], partido=parl.get("partido"), uf=parl.get("uf"),
            diretrizes=QUADRO_ANALITICO,
        ),
    }
