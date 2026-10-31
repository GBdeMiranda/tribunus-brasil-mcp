"""
Publicidade (CF, art. 37): what a citizen can verify about a mandate - receipts, suppliers,
paid self-promotion, amendment destinations and publicly known votes.

Indicators carry absolute magnitude (count, R$) with their base and percentage, and evidence.
Interpretation is left to the calling LLM, guided by INSTRUCAO. All sources are keyless.
"""

import asyncio
import datetime
from typing import Any
from .activity import KNOWN_POSITIONS, _get_deputy_votes, _get_senator_votes
from .cnpj import NOT_FOUND, classify_document, lookup_cnpjs
from .emendas import PRIVATE_GROUPS, RECIPIENT_GROUPS, TIPO_TRANSFERENCIA_ESPECIAL, get_amendments
from .identity import HOUSE_LABELS, resolve_parliamentarian
from .reference import in_minimum_wages, minimum_wage_by_month
from .transparency import (
    _fetch_deputy_year,
    _fetch_senator_year,
    camara_ceap_house_stats,
    is_self_promotion_category,
    senado_ceaps_house_stats,
)
from .utils import ratio

# A supplier whose CNPJ was opened this close to its first payment is reported (as a sign
# that deserves an explanation, not as an irregularity).
RECENTLY_OPENED_DAYS = 180
MAX_EVIDENCE = 15

INSTRUCAO = """Analise o que o cidadão consegue verificar sobre o mandato de {nome} ({casa}, {partido}/{uf}) em {ano}: quanto gastou, com quem, para quê, para onde foram as emendas e como votou.

Para cada indicador:
- Cite quantidade e valor absolutos junto com o percentual e a base.
- Separe o que é regra ou omissão da Casa, que atinge todos (comprovantes_publicados_pela_casa falso, votação secreta, categorias faturadas pela Casa), do que é escolha do parlamentar.
- Avalie a qualidade do gasto (a quem vai, para quê, com que prestação de contas), não o valor.
- Pese explicações plausíveis antes de apontar indício: fornecedor estrangeiro sem CNPJ, CNPJ baixado depois do pagamento, ausência por licença (na Câmara o motivo da ausência não é publicado).
- O gasto com divulgação mostra quanto foi pago, não o conteúdo; diferencie prestação de contas de promoção pessoal só quando houver elementos para isso.
- Nas transferências especiais o uso final do dinheiro não aparece nesta base.

Feche com as perguntas_de_accountability pertinentes."""


def _brl(value: float) -> str:
    formatted = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if value < 0 else ''}R$ {formatted}"


def _n(count: int, singular: str, plural: str) -> str:
    """'1 fornecedor' / '3 fornecedores'."""
    return f"{count} {singular if count == 1 else plural}"


def _magnitude(qtd: int, base_qtd: int, valor: float | None = None, base_valor: float | None = None) -> dict[str, Any]:
    """Absolute figures, their base, and the shares derived from them."""
    result: dict[str, Any] = {"quantidade": qtd, "base_quantidade": base_qtd, "percentual_quantidade": ratio(qtd, base_qtd)}
    if valor is not None:
        result.update({
            "valor": round(valor, 2),
            "base_valor": round(base_valor or 0.0, 2),
            "percentual_valor": ratio(valor, base_valor or 0.0),
        })
    return result


def _receipts_indicator(parl: dict[str, Any], items: list[dict[str, Any]], house: dict[str, Any] | None) -> dict[str, Any]:
    if parl["casa"] == "senado":
        total = sum(i["valor"] for i in items)
        # The Senate's CEAPS data never links a receipt, for any senator.
        return {
            "comprovantes_publicados_pela_casa": False,
            "sem_comprovante": _magnitude(len(items), len(items), total, total),
        }

    institutional = set(house["categorias_institucionais"]) if house else set()
    verifiable = [i for i in items if i["tipo"] not in institutional]
    missing = [i for i in verifiable if not i.get("documento_url")]

    # How common missing receipts are tells whether it is a house practice or an individual one.
    prevalence = None
    if house:
        peers = [p for p in house["parlamentares"].values() if p["valor_comprovavel"] > 0]
        prevalence = {
            "deputados_com_alguma_nota_sem_comprovante": sum(1 for p in peers if p["sem_comprovante_n"] > 0),
            "n_deputados": len(peers),
        }

    return {
        "comprovantes_publicados_pela_casa": True,
        "categorias_faturadas_pela_casa": sorted(institutional),
        "sem_comprovante": _magnitude(
            len(missing), len(verifiable), sum(i["valor"] for i in missing), sum(i["valor"] for i in verifiable)
        ),
        "pratica_na_casa": prevalence,
        "evidencias": [
            {k: i.get(k) for k in ("data", "tipo", "fornecedor", "cnpj_cpf", "valor", "documento_numero")}
            for i in sorted(missing, key=lambda x: -x["valor"])[:MAX_EVIDENCE]
        ],
    }


def _parse_date(value: str | None) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


async def _suppliers_indicator(items: list[dict[str, Any]], institutional: set[str]) -> dict[str, Any]:
    # Centrally billed categories (telephony, SIGEPA tickets...) name the Chamber's own
    # contracts, not suppliers chosen by the office.
    items = [i for i in items if i["tipo"] not in institutional]
    suppliers: dict[str, dict[str, Any]] = {}
    for item in items:
        kind, digits = classify_document(item.get("cnpj_cpf") or "")
        key = digits or f"sem-documento:{item['fornecedor']}"
        s = suppliers.setdefault(key, {
            "tipo_documento": kind, "documento": digits, "fornecedor": item["fornecedor"],
            "valor": 0.0, "notas": [], "categorias": set(),
        })
        s["valor"] += item["valor"]
        s["notas"].append(item)
        s["categorias"].add(item["tipo"])

    cnpj_suppliers = {k: s for k, s in suppliers.items() if s["tipo_documento"] == "cnpj"}
    registry = await lookup_cnpjs(list(cnpj_suppliers))

    total_value = sum(s["valor"] for s in suppliers.values())
    by_doc_type = {}
    for kind in ("cnpj", "cpf", "nao_informado", "invalido"):
        group = [s for s in suppliers.values() if s["tipo_documento"] == kind]
        by_doc_type[kind] = _magnitude(len(group), len(suppliers), sum(s["valor"] for s in group), total_value)

    signals: dict[str, list[dict[str, Any]]] = {
        "situacao_irregular_na_data_do_pagamento": [],
        "cnpj_nao_localizado_na_receita": [],
        "aberto_pouco_antes_do_primeiro_pagamento": [],
    }
    notes_after_irregular: dict[str, list[dict[str, Any]]] = {}
    unverified = 0
    evidence: dict[str, dict[str, Any]] = {}

    for cnpj, s in cnpj_suppliers.items():
        data = registry.get(cnpj)
        if data is None:
            unverified += 1
            continue
        dates = [d for d in (_parse_date(n.get("data")) for n in s["notas"]) if d]
        first_payment = min(dates) if dates else None
        reasons = []

        if data == NOT_FOUND:
            signals["cnpj_nao_localizado_na_receita"].append(s)
            reasons.append("CNPJ não localizado na base da Receita Federal")
        else:
            status_date = _parse_date(data.get("data_situacao"))
            if data.get("situacao") and data["situacao"] != "ATIVA" and status_date:
                # Only payments made on/after the status change count: a company closed in
                # 2026 was perfectly regular when paid in 2025.
                affected = [n for n in s["notas"] if (_parse_date(n.get("data")) or datetime.date.min) >= status_date]
                if affected:
                    signals["situacao_irregular_na_data_do_pagamento"].append(s)
                    notes_after_irregular[cnpj] = affected
                    reasons.append(
                        f"situação {data['situacao']} desde {status_date.isoformat()} "
                        f"({len(affected)} nota(s) posteriores, {_brl(sum(n['valor'] for n in affected))})"
                    )
            opened = _parse_date(data.get("data_abertura"))
            if opened and first_payment and 0 <= (first_payment - opened).days <= RECENTLY_OPENED_DAYS:
                signals["aberto_pouco_antes_do_primeiro_pagamento"].append(s)
                reasons.append(
                    f"aberto em {opened.isoformat()}, {(first_payment - opened).days} dias antes do primeiro pagamento"
                )

        if reasons:
            evidence[cnpj] = {
                "fornecedor": s["fornecedor"],
                "cnpj": cnpj,
                "razao_social": None if data == NOT_FOUND else data.get("razao_social"),
                "cnae_principal": None if data == NOT_FOUND else data.get("cnae_principal"),
                "categorias_ceap": sorted(s["categorias"]),
                "valor_pago": round(s["valor"], 2),
                "notas": len(s["notas"]),
                "primeiro_pagamento": first_payment.isoformat() if first_payment else None,
                "motivos": reasons,
            }

    def signal_summary(name: str) -> dict[str, Any]:
        flagged = signals[name]
        if name == "situacao_irregular_na_data_do_pagamento":
            affected = [n for s in flagged for n in notes_after_irregular[s["documento"]]]
            value = sum(n["valor"] for n in affected)
            return {**_magnitude(len(flagged), len(cnpj_suppliers), value, total_value), "notas_afetadas": len(affected)}
        return _magnitude(len(flagged), len(cnpj_suppliers), sum(s["valor"] for s in flagged), total_value)

    top = sorted(cnpj_suppliers.values(), key=lambda s: -s["valor"])[:5]
    undocumented = sorted(
        (s for s in suppliers.values() if s["tipo_documento"] in ("nao_informado", "invalido")),
        key=lambda s: -s["valor"],
    )
    return {
        "fornecedores_por_tipo_de_documento": by_doc_type,
        "verificacao_receita": {"cnpjs_verificados": len(cnpj_suppliers) - unverified, "sem_resposta": unverified},
        "sinais": {name: signal_summary(name) for name in signals},
        "evidencias": list(evidence.values())[:MAX_EVIDENCE],
        "fornecedores_sem_documento_valido": [
            {
                "fornecedor": s["fornecedor"],
                "documento_registrado": s["notas"][0].get("cnpj_cpf"),
                "tipo_documento": s["tipo_documento"],
                "categorias_ceap": sorted(s["categorias"]),
                "valor_pago": round(s["valor"], 2),
                "notas": len(s["notas"]),
            }
            for s in undocumented[:MAX_EVIDENCE]
        ],
        "maiores_fornecedores": [
            {
                "fornecedor": s["fornecedor"],
                "cnpj": s["documento"],
                "cnae_principal": (registry.get(s["documento"]) or {}).get("cnae_principal"),
                "categorias_ceap": sorted(s["categorias"]),
                "valor_pago": round(s["valor"], 2),
                "notas": len(s["notas"]),
            }
            for s in top
        ],
        "criterio_aberto_pouco_antes_dias": RECENTLY_OPENED_DAYS,
    }


def _self_promotion_indicator(
    parl: dict[str, Any], items: list[dict[str, Any]], house: dict[str, Any] | None, wages: dict[str, float]
) -> dict[str, Any]:
    promo = [i for i in items if is_self_promotion_category(i["tipo"])]
    total = sum(i["valor"] for i in items)
    value = sum(i["valor"] for i in promo)

    suppliers: dict[str, dict[str, Any]] = {}
    for i in promo:
        s = suppliers.setdefault(i["cnpj_cpf"] or i["fornecedor"], {
            "fornecedor": i["fornecedor"], "cnpj_cpf": i["cnpj_cpf"], "valor": 0.0, "notas": 0, "comprovantes": [],
        })
        s["valor"] += i["valor"]
        s["notas"] += 1
        if i.get("documento_url") and len(s["comprovantes"]) < 3:
            s["comprovantes"].append(i["documento_url"])

    return {
        "gasto": _magnitude(len(promo), len(items), value, total),
        "em_salarios_minimos": in_minimum_wages(promo, wages) if promo else 0.0,
        "fornecedores": [
            {**s, "valor": round(s["valor"], 2)}
            for s in sorted(suppliers.values(), key=lambda x: -x["valor"])[:5]
        ],
    }


def _amendments_indicator(data: dict[str, Any], year: int) -> dict[str, Any]:
    if not data.get("localizado"):
        return {
            "localizado": False,
            "motivo": data.get("erro") or "Autor não localizado no Portal da Transparência pelo nome parlamentar ou civil.",
            **({"codigos_autor": data["codigos_autor"]} if data.get("codigos_autor") else {}),
            "metadados": data.get("metadados"),
        }

    individual = [e for e in data["emendas"] if (e["tipo"] or "").startswith("Emenda Individual")]
    special = [e for e in individual if e["tipo"] == TIPO_TRANSFERENCIA_ESPECIAL]
    payments = data["pagamentos"]
    received = sum(p["valor"] for p in payments)

    groups = {}
    for group in RECIPIENT_GROUPS:
        rows = [p for p in payments if p["grupo"] == group]
        value = sum(p["valor"] for p in rows)
        groups[group] = {
            "favorecidos_distintos": len({p["documento_favorecido"] or p["favorecido"] for p in rows}),
            "valor": round(value, 2),
            "percentual_valor": ratio(value, received),
        }

    private: dict[str, dict[str, Any]] = {}
    for p in payments:
        if p["grupo"] not in PRIVATE_GROUPS:
            continue
        e = private.setdefault(p["documento_favorecido"] or p["favorecido"], {
            "favorecido": p["favorecido"], "cnpj": p["documento_favorecido"], "natureza_juridica": p["natureza_juridica"],
            "municipio": p["municipio"], "uf": p["uf"], "valor": 0.0,
        })
        e["valor"] += p["valor"]

    return {
        "localizado": True,
        "nome_no_portal": data["nome_no_portal"],
        "emendas_individuais": {
            "quantidade": len(individual),
            "empenhado": round(sum(e["empenhado"] for e in individual), 2),
            "pago": round(sum(e["pago"] for e in individual), 2),
        },
        "transferencias_especiais": {
            **_magnitude(len(special), len(individual), sum(e["empenhado"] for e in special),
                         sum(e["empenhado"] for e in individual)),
            "pago": round(sum(e["pago"] for e in special), 2),
        },
        "destino_dos_pagamentos": {"total_recebido": round(received, 2), "por_tipo_de_favorecido": groups},
        "referencia_autores": (data.get("referencia_autores") or {}).get(year),
        "maiores_favorecidos_privados": [
            {**e, "valor": round(e["valor"], 2)} for e in sorted(private.values(), key=lambda x: -x["valor"])[:10]
        ],
        "emendas": sorted(individual, key=lambda e: -e["empenhado"])[:MAX_EVIDENCE],
        "metadados": data.get("metadados"),
    }


def _votes_indicator(votes: dict[str, Any]) -> dict[str, Any]:
    participation = votes.get("participacao_plenario")
    if not participation:
        return {"erro": votes.get("error") or "Votações indisponíveis."}

    base = participation["votacoes_nominais"]
    distribution = participation.get("distribuicao_plenario", {})
    known = sum(n for pos, n in distribution.items() if pos in KNOWN_POSITIONS)
    unknown = {
        # Counted from the senator's own 'Votou' code, so an absence during a secret vote
        # is counted once, as an absence.
        "votacao_secreta": distribution.get("Votou", 0),
        "presente_sem_votar": participation.get("presente_sem_votar", 0),
        "presidindo_a_sessao": sum(n for pos, n in distribution.items() if pos.startswith(("Artigo 17", "Presidente"))),
    }
    if "sem_registro_de_voto" in participation:  # Câmara: absence reason not published
        unknown["ausente_ou_sem_registro"] = participation["sem_registro_de_voto"]
    else:
        unknown["ausencia_justificada"] = participation.get("ausencia_justificada", 0)
        unknown["ausencia_nao_justificada"] = participation.get("ausencia_nao_justificada", 0)

    return {
        "posicao_conhecida": _magnitude(known, base),
        "posicao_desconhecida": {k: v for k, v in unknown.items() if v},
        "metadados": votes.get("metadados"),
    }


def _accountability_questions(ind: dict[str, Any]) -> list[str]:
    """Plain-language questions generated only where a figure is above zero."""
    questions = []
    receipts = ind["comprovacao_da_cota"]
    if receipts["comprovantes_publicados_pela_casa"] and receipts["sem_comprovante"]["quantidade"]:
        m = receipts["sem_comprovante"]
        questions.append(
            f"Por que {_n(m['quantidade'], 'despesa da cota', 'despesas da cota')}, somando {_brl(m['valor'])}, "
            "não têm comprovante publicado? O gabinete pode divulgá-los?"
        )
    signals = ind["rastreabilidade_fornecedores"]["sinais"]
    irregular = signals["situacao_irregular_na_data_do_pagamento"]
    if irregular["quantidade"]:
        questions.append(
            f"Por que o gabinete pagou {_brl(irregular['valor'])} "
            f"({_n(irregular['notas_afetadas'], 'nota', 'notas')}) a "
            f"{_n(irregular['quantidade'], 'fornecedor', 'fornecedores')} com CNPJ que já não estava ativo na "
            "data do pagamento?"
        )
    if signals["cnpj_nao_localizado_na_receita"]["quantidade"]:
        m = signals["cnpj_nao_localizado_na_receita"]
        questions.append(
            f"Como foram pagos {_brl(m['valor'])} a {_n(m['quantidade'], 'fornecedor', 'fornecedores')} com CNPJ "
            "que não consta na Receita Federal?"
        )
    if signals["aberto_pouco_antes_do_primeiro_pagamento"]["quantidade"]:
        m = signals["aberto_pouco_antes_do_primeiro_pagamento"]
        subject = (
            "foi escolhido o fornecedor aberto" if m["quantidade"] == 1
            else f"foram escolhidos os {m['quantidade']} fornecedores abertos"
        )
        questions.append(f"Como {subject} pouco antes de receber pagamentos da cota ({_brl(m['valor'])})?")
    by_doc = ind["rastreabilidade_fornecedores"]["fornecedores_por_tipo_de_documento"]
    if by_doc["invalido"]["quantidade"]:
        m = by_doc["invalido"]
        questions.append(
            f"Por que {_n(m['quantidade'], 'fornecedor', 'fornecedores')}, que receberam {_brl(m['valor'])}, "
            "foram registrados com CNPJ/CPF inválido?"
        )
    if by_doc["nao_informado"]["quantidade"]:
        m = by_doc["nao_informado"]
        questions.append(
            f"Quem são os fornecedores sem CNPJ/CPF informado ({_n(m['quantidade'], 'fornecedor', 'fornecedores')}, "
            f"{_brl(m['valor'])})? Se forem estrangeiros, qual foi o serviço contratado?"
        )
    promo = ind["divulgacao_paga"]
    if promo["gasto"]["quantidade"]:
        questions.append(
            f"Onde estão publicadas as peças pagas com os {_brl(promo['gasto']['valor'])} de "
            "divulgação do mandato, e o que informam ao cidadão?"
        )
    emendas = ind["transparencia_emendas"]
    if emendas.get("localizado"):
        te = emendas["transferencias_especiais"]
        if te["quantidade"]:
            subject = (
                "está o plano de trabalho da transferência especial" if te["quantidade"] == 1
                else f"estão os planos de trabalho das {te['quantidade']} transferências especiais"
            )
            questions.append(f"Onde {subject} ({_brl(te['valor'])} empenhados) e em que o dinheiro foi gasto?")
        unidentified = emendas["destino_dos_pagamentos"]["por_tipo_de_favorecido"]["nao_identificado"]
        if unidentified["valor"]:
            questions.append(
                f"Quem recebeu os {_brl(unidentified['valor'])} de emendas cujo favorecido não está identificado?"
            )
    votes = ind["voto_publicamente_conhecido"]
    if "posicao_conhecida" in votes:
        m = votes["posicao_conhecida"]
        unknown = votes["posicao_desconhecida"]
        if unknown.get("ausente_ou_sem_registro"):
            questions.append(
                f"Em {unknown['ausente_ou_sem_registro']} das {m['base_quantidade']} votações nominais do Plenário "
                "não há registro de voto. Quais foram os motivos?"
            )
        if unknown.get("ausencia_nao_justificada"):
            questions.append(
                "Por que não compareceu, sem justificativa registrada, a "
                f"{_n(unknown['ausencia_nao_justificada'], 'votação nominal', 'votações nominais')} do Plenário "
                f"(de {m['base_quantidade']})?"
            )
        if unknown.get("presente_sem_votar"):
            questions.append(
                f"Em {_n(unknown['presente_sem_votar'], 'votação nominal', 'votações nominais')} esteve presente "
                "sem registrar voto. Qual era sua posição?"
            )
    return questions


async def audit_publicity(
    name: str | None = None,
    house: str = "auto",
    year: int | None = None,
    parliamentarian_id: int | None = None,
) -> dict[str, Any]:
    """
    Publicity audit: receipts, supplier traceability, paid self-promotion, amendment
    traceability and publicly known votes, each with absolute magnitude and evidence.
    """
    parl = await resolve_parliamentarian(name, house=house, parliamentarian_id=parliamentarian_id)
    if "error" in parl:
        return parl

    # Default to the last complete year: a partial year understates everything.
    target_year = year or datetime.date.today().year - 1
    is_senate = parl["casa"] == "senado"

    (items, expenses_meta), house_stats, wages, votes, amendments = await asyncio.gather(
        _fetch_senator_year(parl["id"], target_year) if is_senate else _fetch_deputy_year(parl["id"], target_year),
        senado_ceaps_house_stats(target_year) if is_senate else camara_ceap_house_stats(target_year),
        minimum_wage_by_month(),
        (_get_senator_votes if is_senate else _get_deputy_votes)(parl, limit=0, year=target_year),
        get_amendments([parl["nome"], parl.get("nome_civil")], [target_year]),
    )

    institutional = set((house_stats or {}).get("categorias_institucionais", []))
    indicadores = {
        "comprovacao_da_cota": _receipts_indicator(parl, items, house_stats),
        "rastreabilidade_fornecedores": await _suppliers_indicator(items, institutional),
        "divulgacao_paga": _self_promotion_indicator(parl, items, house_stats, wages),
        "transparencia_emendas": _amendments_indicator(amendments, target_year),
        "voto_publicamente_conhecido": _votes_indicator(votes),
    }

    return {
        "parlamentar": parl["nome"],
        "id": parl["id"],
        "casa": HOUSE_LABELS[parl["casa"]],
        "partido": parl.get("partido"),
        "uf": parl.get("uf"),
        "ano_referencia": target_year,
        "indicadores": indicadores,
        "perguntas_de_accountability": _accountability_questions(indicadores),
        "instrucao": INSTRUCAO.format(
            nome=parl["nome"], casa=HOUSE_LABELS[parl["casa"]], partido=parl.get("partido"),
            uf=parl.get("uf"), ano=target_year,
        ),
        "metadados": {
            "despesas": {**expenses_meta, "n_registros": len(items)},
            "referencia_casa": {k: house_stats.get(k) for k in ("fonte", "idade_arquivo_horas")} if house_stats else None,
            "salario_minimo": "Banco Central, SGS série 1619",
            "periodo": str(target_year),
        },
    }
