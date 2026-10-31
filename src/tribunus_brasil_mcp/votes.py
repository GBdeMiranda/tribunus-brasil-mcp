"""
Every merit vote a parliamentarian cast in plenary, classified into thematic evaluation axes
through each house's own official thematic classification of the bill, with the context
needed to judge it: result and margin, party majority, government orientation, absences in
contested votes and positioning across governments.
"""

import asyncio
import collections
import datetime
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any
from .utils import cache_max_age, fetch_cached_file, iter_csv_rows, make_request, ratio

CAMARA_BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
CAMARA_FILES = "https://dadosabertos.camara.leg.br/arquivos/{name}/csv/{name}-{year}.csv"
SENADO_BASE_URL = "https://legis.senado.leg.br/dadosabertos"
JSON_HEADERS = {"Accept": "application/json"}

EIXOS_AVALIACAO = {
    "trabalho_renda_e_previdencia": "Trabalho, Renda, Previdência e Tributação",
    "servicos_publicos_e_bem_estar": "Serviços Públicos, Direitos Sociais e Meio Ambiente",
    "patrimonio_publico_e_soberania": "Patrimônio Público, Infraestrutura e Soberania Nacional",
    "institucional_e_administracao_publica": "Administração Pública, Justiça e Garantias Constitucionais",
    "transparencia_e_controle_social": "Transparência, Moralidade e Controle Social",
}

# No official theme covers transparency, so bills are placed in this axis by what their
# summary regulates (same rule for every bill).
TRANSPARENCIA = re.compile(
    r"transpar[eê]ncia|acesso [aà] informa[cç]|sigil|prest[aã]o de contas|publicidade d|"
    r"emendas? (parlamentar|individua|de bancada|de comiss|de relator)|transfer[eê]ncias? especia|"
    r"controle (social|externo)|tribunal de contas|improbidade|corrup[cç]|lavagem de dinheiro|"
    r"imunidade parlamentar|inviolabilidade|prerrogativas? parlamentar|prerrogativas? (dos|de) (membros do congresso|parlamentar)|"
    r"foro (por prerrogativa|privilegiado)|"
    r"voto (aberto|secreto)|lobby|conflito de interesses",
    re.I,
)

# Câmara official themes (referencias/proposicoes/codTema) -> evaluation axes.
CAMARA_TEMA_EIXOS: dict[str, list[str]] = {
    "Trabalho e Emprego": ["trabalho_renda_e_previdencia"],
    "Previdência e Assistência Social": ["trabalho_renda_e_previdencia"],
    "Economia": ["trabalho_renda_e_previdencia"],
    "Finanças Públicas e Orçamento": ["trabalho_renda_e_previdencia"],
    "Direito e Defesa do Consumidor": ["trabalho_renda_e_previdencia"],
    "Saúde": ["servicos_publicos_e_bem_estar"],
    "Educação": ["servicos_publicos_e_bem_estar"],
    "Viação, Transporte e Mobilidade": ["servicos_publicos_e_bem_estar", "trabalho_renda_e_previdencia"],
    "Cidades e Desenvolvimento Urbano": ["servicos_publicos_e_bem_estar"],
    "Direitos Humanos e Minorias": ["servicos_publicos_e_bem_estar", "institucional_e_administracao_publica"],
    "Meio Ambiente e Desenvolvimento Sustentável": ["servicos_publicos_e_bem_estar"],
    "Estrutura Fundiária": ["servicos_publicos_e_bem_estar"],
    "Agricultura, Pecuária, Pesca e Extrativismo": ["servicos_publicos_e_bem_estar", "trabalho_renda_e_previdencia"],
    "Arte, Cultura e Religião": ["servicos_publicos_e_bem_estar"],
    "Esporte e Lazer": ["servicos_publicos_e_bem_estar"],
    "Turismo": ["trabalho_renda_e_previdencia", "servicos_publicos_e_bem_estar"],
    "Energia, Recursos Hídricos e Minerais": ["patrimonio_publico_e_soberania", "servicos_publicos_e_bem_estar"],
    "Indústria, Comércio e Serviços": ["trabalho_renda_e_previdencia", "patrimonio_publico_e_soberania"],
    "Ciência, Tecnologia e Inovação": ["patrimonio_publico_e_soberania", "trabalho_renda_e_previdencia"],
    "Relações Internacionais e Comércio Exterior": ["patrimonio_publico_e_soberania"],
    "Defesa e Segurança": ["institucional_e_administracao_publica", "patrimonio_publico_e_soberania"],
    "Administração Pública": ["institucional_e_administracao_publica", "trabalho_renda_e_previdencia"],
    "Direito Constitucional": ["institucional_e_administracao_publica"],
    "Direito Penal e Processual Penal": ["institucional_e_administracao_publica"],
    "Direito e Justiça": ["institucional_e_administracao_publica"],
    "Direito Civil e Processual Civil": ["institucional_e_administracao_publica"],
    "Política, Partidos e Eleições": ["institucional_e_administracao_publica"],
    "Processo Legislativo e Atuação Parlamentar": ["institucional_e_administracao_publica"],
    "Comunicações": ["institucional_e_administracao_publica"],
}

# Senate hierarchical classes (DescricaoClasseHierarquica) -> evaluation axes; longest prefix wins.
SENADO_CLASSE_EIXOS: dict[str, list[str]] = {
    "Economia e Desenvolvimento": ["trabalho_renda_e_previdencia"],
    "Economia e Desenvolvimento / Agropecuária e Abastecimento": ["servicos_publicos_e_bem_estar", "trabalho_renda_e_previdencia"],
    "Orçamento Público": ["trabalho_renda_e_previdencia"],
    "Política Social": ["servicos_publicos_e_bem_estar"],
    "Política Social / Trabalho e Emprego": ["trabalho_renda_e_previdencia"],
    "Política Social / Previdência Social": ["trabalho_renda_e_previdencia"],
    "Política Social / Proteção Social": ["trabalho_renda_e_previdencia", "servicos_publicos_e_bem_estar"],
    "Administração Pública": ["institucional_e_administracao_publica", "trabalho_renda_e_previdencia"],
    "Administração Pública / Terceiro Setor, Parcerias Público-Privadas e Desestatização": ["patrimonio_publico_e_soberania", "trabalho_renda_e_previdencia"],
    "Jurídico": ["institucional_e_administracao_publica"],
    "Organização do Estado": ["institucional_e_administracao_publica"],
    "Meio Ambiente": ["servicos_publicos_e_bem_estar"],
    "Infraestrutura": ["patrimonio_publico_e_soberania", "servicos_publicos_e_bem_estar"],
    "Soberania, Defesa Nacional e Ordem Pública": ["patrimonio_publico_e_soberania", "institucional_e_administracao_publica"],
    "Relações Internacionais": ["patrimonio_publico_e_soberania"],
    "Relações Exteriores": ["patrimonio_publico_e_soberania"],
}

# Procedural roll calls (urgency, agenda, interstice, final wording...) say little about content.
PROCEDURAL = re.compile(r"requerimento|reda[cç][aã]o final|prefer[eê]ncia|recurso|apreci[aç][aã]o preliminar|interst[ií]cio", re.I)
MAIN_TEXT = re.compile(
    r"(aprovad|rejeitad)[oa]s?,?\s*(,|em (primeiro|segundo) turno,?)?\s*(o|a|as)?\s*"
    r"(projeto|proposta|substitutivo|subemenda substitutiva|emenda aglutinativa substitutiva|medida provis|parecer|mat[eé]ria)"
    r"|em (primeiro|segundo) turno|^aprovadas\.",
    re.I,
)
SENADO_NON_MERIT_TYPES = {"REQ", "RQS", "MSF", "OFS", "MSG"}  # requests and nominations of authorities

GOVERNOS = [
    ("2016-05-12", "Temer"),
    ("2019-01-01", "Bolsonaro"),
    ("2023-01-01", "Lula"),
]
CONSENSUS_SHARE = 0.85  # one side with at least this share of the votes cast
CONTESTED_SHARE = 0.60  # winning side with at most this share
ITEMS_PER_AXIS = 20

YES, NO, ABSTAIN = "Sim", "Não", "Abstenção"


def _government(date: str) -> str:
    name = "anterior a 2016"
    for start, label in GOVERNOS:
        if date >= start:
            name = label
    return name


def _norm_vote(v: str | None) -> str | None:
    if v in (YES, NO, ABSTAIN):
        return v
    if v in ("Obstrução", "P-OD"):
        return NO
    return None


def _senado_position(raw: str | None) -> str:
    vote = _norm_vote(raw)
    if vote:
        return vote
    if raw and raw.startswith("Presidente"):
        return "presidindo"
    if raw == "P-NRV":
        return "presente_sem_votar"
    return "ausente"


def legislatura(date: str) -> int:
    """Legislature number for a date (57 = Feb/2023 to Jan/2027)."""
    d = datetime.date.fromisoformat(date[:10])
    return 55 + ((d.year - (1 if d.month < 2 else 0)) - 2015) // 4


def _counts_summary(counts: collections.Counter) -> dict[str, Any]:
    yes, no = counts.get(YES, 0), counts.get(NO, 0)
    total = yes + no
    return {"sim": yes, "nao": no, "abstencao": counts.get(ABSTAIN, 0),
            "maioria": ratio(max(yes, no), total)}


def _majority(counter: collections.Counter) -> str | None:
    yes, no = counter.get(YES, 0), counter.get(NO, 0)
    if not yes and not no:
        return None
    return YES if yes > no else NO if no > yes else "empate"


def _content_axes(ementa: str | None) -> set[str]:
    return {"transparencia_e_controle_social"} if ementa and TRANSPARENCIA.search(ementa) else set()


def _axes_for_camara(temas: list[str], ementa: str | None = None) -> list[str]:
    return sorted({axis for t in temas for axis in CAMARA_TEMA_EIXOS.get(t, [])} | _content_axes(ementa))


def _axes_for_senado(classes: list[str], ementa: str | None = None) -> list[str]:
    axes = _content_axes(ementa)
    for c in classes:
        parts = c.split(" / ")
        for depth in range(len(parts), 0, -1):
            key = " / ".join(parts[:depth])
            if key in SENADO_CLASSE_EIXOS:
                axes.update(SENADO_CLASSE_EIXOS[key])
                break
    return sorted(axes)


# ---------------------------------------------------------------- Câmara ---------

_camara_year_cache: dict[tuple[int, float], dict[str, Any]] = {}
_camara_themes_cache: dict[tuple[int, float], dict[str, list[str]]] = {}


def _scan_camara_year(votos: Path, votacoes: Path, proposicoes: Path, orientacoes: Path) -> dict[str, Any]:
    """One pass over a year's files: plenary merit roll calls, all deputies' votes, context."""
    roll_calls: dict[str, dict[str, Any]] = {}
    for r in iter_csv_rows(votacoes):
        if r.get("siglaOrgao") != "PLEN":
            continue
        desc = re.sub(r"\s+", " ", r.get("descricao") or "").strip()
        if PROCEDURAL.search(desc):
            continue
        roll_calls[r["id"]] = {"data": (r.get("dataHoraRegistro") or r.get("data") or "")[:10], "descricao": desc}

    links: dict[str, dict[str, str]] = {}
    for r in iter_csv_rows(proposicoes):
        vid = r.get("idVotacao")
        if vid in roll_calls and vid not in links:
            links[vid] = {
                "id": r.get("proposicao_id"), "materia": r.get("proposicao_titulo"),
                "ementa": r.get("proposicao_ementa"), "ano": r.get("proposicao_ano"),
            }

    government = {r["idVotacao"]: r.get("orientacao") for r in iter_csv_rows(orientacoes)
                  if r.get("siglaBancada") == "Governo" and r.get("idVotacao") in roll_calls}

    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    party_counts: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    by_deputy: dict[str, dict[str, tuple[str, str]]] = collections.defaultdict(dict)
    for r in iter_csv_rows(votos):
        vid = r.get("idVotacao")
        if vid not in roll_calls:
            continue
        vote = _norm_vote(r.get("voto"))
        party = r.get("deputado_siglaPartido") or ""
        if vote:
            counts[vid][vote] += 1
            party_counts[(vid, party)][vote] += 1
        by_deputy[r.get("deputado_id") or ""][vid] = (r.get("voto") or "", party)

    # The votações file also lists symbolic votes, which leave no individual record.
    nominal = {vid: rc for vid, rc in roll_calls.items() if vid in counts}
    return {
        "roll_calls": nominal, "links": links, "government": government,
        "counts": counts, "party_counts": party_counts, "by_deputy": by_deputy,
    }


async def _camara_year(year: int) -> dict[str, Any] | None:
    max_age = cache_max_age(year)
    names = ("votacoesVotos", "votacoes", "votacoesProposicoes", "votacoesOrientacoes")
    paths = await asyncio.gather(*(
        fetch_cached_file(CAMARA_FILES.format(name=n, year=year), f"camara/{n}-{year}.csv", max_age) for n in names
    ))
    if not all(paths):
        return None
    key = (year, max(p.stat().st_mtime for p in paths))
    if key not in _camara_year_cache:
        _camara_year_cache[key] = await asyncio.to_thread(_scan_camara_year, *paths)
    return _camara_year_cache[key]


def _scan_themes(path: Path) -> dict[str, list[str]]:
    themes: dict[str, list[str]] = collections.defaultdict(list)
    for r in iter_csv_rows(path):
        pid = (r.get("uriProposicao") or "").rsplit("/", 1)[-1]
        if pid and r.get("tema"):
            themes[pid].append(r["tema"])
    return themes


async def _camara_themes(prop_years: set[int]) -> dict[str, list[str]]:
    """Official themes of every proposition, read from the yearly files of the years involved."""
    async def load(year: int) -> dict[str, list[str]]:
        path = await fetch_cached_file(
            CAMARA_FILES.format(name="proposicoesTemas", year=year), f"camara/proposicoesTemas-{year}.csv", cache_max_age(year)
        )
        if not path:
            return {}
        key = (year, path.stat().st_mtime)
        if key not in _camara_themes_cache:
            _camara_themes_cache[key] = await asyncio.to_thread(_scan_themes, path)
        return _camara_themes_cache[key]

    merged: dict[str, list[str]] = {}
    for part in await asyncio.gather(*(load(y) for y in sorted(prop_years))):
        merged.update(part)
    return merged


async def _camara_status_timeline(dep_id: int) -> list[dict[str, Any]]:
    data = await make_request(f"{CAMARA_BASE_URL}/deputados/{dep_id}/historico") or {}
    return sorted(data.get("dados", []), key=lambda h: h.get("dataHora") or "")


def _in_office(timeline: list[dict[str, Any]], date: str) -> bool:
    before = [h for h in timeline if (h.get("dataHora") or "")[:10] <= date]
    if not before:
        return False
    status = before[-1].get("situacao")
    return before[-1].get("idLegislatura") == legislatura(date) and (status is None or status.lower().startswith("exerc"))


async def _camara_records(dep_id: int, years: list[int]) -> list[dict[str, Any]]:
    loaded = await asyncio.gather(*(_camara_year(y) for y in years))
    loaded = [y for y in loaded if y]
    timeline = await _camara_status_timeline(dep_id)
    prop_years = {int(link["ano"]) for y in loaded for link in y["links"].values() if (link.get("ano") or "").isdigit()}
    themes = await _camara_themes(prop_years)

    records = []
    target = str(dep_id)
    for y in loaded:
        own = y["by_deputy"].get(target, {})
        for vid, rc in y["roll_calls"].items():
            link = y["links"].get(vid)
            if not link:
                continue
            raw, party = own.get(vid, (None, None))
            if raw is None and not _in_office(timeline, rc["data"]):
                continue
            temas = themes.get(link["id"] or "", [])
            records.append({
                "votacao_id": vid,
                "data": rc["data"],
                "descricao": rc["descricao"],
                "texto_principal": bool(MAIN_TEXT.search(rc["descricao"])),
                "proposicao_id": link["id"],
                "materia": link["materia"],
                "ementa": link["ementa"],
                "temas": temas,
                "eixos": _axes_for_camara(temas, link["ementa"]),
                "voto_registrado": raw,
                "voto": _norm_vote(raw) if raw is not None else "ausente",
                "resultado": _counts_summary(y["counts"][vid]),
                "maioria_do_partido": _majority(y["party_counts"][(vid, party)]) if party else None,
                "orientacao_governo": y["government"].get(vid) or None,
                "url": f"{CAMARA_BASE_URL}/votacoes/{vid}",
            })
    return records


# ---------------------------------------------------------------- Senado ---------

async def _senado_json(url: str, filename: str, max_age: int, params: dict[str, Any] | None = None) -> Any:
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    path = await fetch_cached_file(full, filename, max_age, headers=JSON_HEADERS)
    if not path:
        return None
    try:
        return await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
    except ValueError:
        return None


async def _senado_classes(code: int) -> list[str]:
    data = await _senado_json(f"{SENADO_BASE_URL}/materia/{code}", f"senado/materia-{code}.json", 30 * 24 * 3600) or {}
    cl = (data.get("DetalheMateria", {}).get("Materia", {}).get("Classificacoes") or {}).get("Classificacao", [])
    cl = [cl] if isinstance(cl, dict) else cl
    return [c.get("DescricaoClasseHierarquica") for c in cl if c.get("DescricaoClasseHierarquica")]


async def _senado_records(code: int, years: list[int]) -> list[dict[str, Any]]:
    per_year = await asyncio.gather(*(
        _senado_json(f"{SENADO_BASE_URL}/votacao", f"senado/votacoes-senador-{code}-{y}.json", cache_max_age(y),
                     params={"codigoParlamentar": code, "dataInicio": f"{y}-01-01", "dataFim": f"{y}-12-31"})
        for y in years
    ))
    own = [v for data in per_year for v in (data or []) if isinstance(v, dict)]
    own = [v for v in own if (v.get("sigla") or "") not in SENADO_NON_MERIT_TYPES and v.get("votacaoSecreta") != "S"]

    days = sorted({v["dataSessao"] for v in own if v.get("dataSessao")})
    day_data = await asyncio.gather(*(
        _senado_json(f"{SENADO_BASE_URL}/votacao", f"senado/votacoes-{d}.json", cache_max_age(int(d[:4])),
                     params={"dataInicio": d, "dataFim": d})
        for d in days
    ))
    full = {v["codigoSessaoVotacao"]: v for data in day_data for v in (data or []) if isinstance(v, dict)}
    materias = sorted({v["codigoMateria"] for v in own if v.get("codigoMateria")})
    classes = dict(zip(materias, await asyncio.gather(*(_senado_classes(c) for c in materias))))

    records = []
    for v in own:
        mine = next((x for x in v.get("votos", []) if str(x.get("codigoParlamentar")) == str(code)), {})
        raw = mine.get("siglaVotoParlamentar")
        party = mine.get("siglaPartidoParlamentar")
        everyone = full.get(v["codigoSessaoVotacao"], {}).get("votos", [])
        counts = collections.Counter(_norm_vote(x.get("siglaVotoParlamentar")) for x in everyone)
        party_counter = collections.Counter(
            _norm_vote(x.get("siglaVotoParlamentar")) for x in everyone if x.get("siglaPartidoParlamentar") == party
        )
        cls = classes.get(v.get("codigoMateria"), [])
        desc = v.get("descricaoVotacao") or ""
        records.append({
            "votacao_id": v["codigoSessaoVotacao"],
            "data": v.get("dataSessao"),
            "descricao": desc,
            "texto_principal": not re.search(r"destacad|emenda n|requerimento", desc, re.I),
            "proposicao_id": v.get("codigoMateria"),
            "materia": v.get("identificacao"),
            "ementa": v.get("ementa"),
            "temas": cls,
            "eixos": _axes_for_senado(cls, v.get("ementa")),
            "voto_registrado": raw,
            "voto": _senado_position(raw),
            "resultado": _counts_summary(counts),
            "maioria_do_partido": _majority(party_counter) if everyone else None,
            "orientacao_governo": None,
            "url": f"{SENADO_BASE_URL}/votacao?codigoParlamentar={code}&dataInicio={v.get('dataSessao')}&dataFim={v.get('dataSessao')}",
        })
    return records


# ---------------------------------------------------------------- analysis -------

def _item(records: list[dict[str, Any]]) -> dict[str, Any]:
    """One entry per bill: the main-text vote plus a summary of the other merit votes on it."""
    main = next((r for r in records if r["texto_principal"]), None) or max(
        records, key=lambda r: r["resultado"]["sim"] + r["resultado"]["nao"]
    )
    others = [r for r in records if r is not main]
    diverged = [r for r in records if r["voto"] in (YES, NO) and r["maioria_do_partido"] in (YES, NO)
                and r["voto"] != r["maioria_do_partido"]]
    return {
        "materia": main["materia"],
        "ementa": (main["ementa"] or "")[:220],
        "temas": main["temas"],
        "data": main["data"],
        "votacao": main["descricao"][:120],
        "voto": main["voto"],
        "resultado": main["resultado"],
        "maioria_do_partido": main["maioria_do_partido"],
        "orientacao_governo": main["orientacao_governo"],
        "outras_votacoes_de_merito": len(others),
        "votos_divergentes_do_partido": len(diverged),
        "fonte": main["url"],
    }


def _salience(item: dict[str, Any]) -> tuple:
    contested = (item["resultado"]["maioria"] or 1.0) <= CONTESTED_SHARE
    dissent = item["votos_divergentes_do_partido"] > 0
    absent = item["voto"] == "ausente"
    return (not (contested or dissent or absent), item["resultado"]["maioria"] or 1.0, item["data"])


def positioning_by_government(records: list[dict[str, Any]]) -> dict[str, Any]:
    """How the parliamentarian positions relative to government, party and the house, per government."""
    periods: dict[str, dict[str, int]] = collections.defaultdict(lambda: collections.defaultdict(int))
    for r in records:
        if r["voto"] not in (YES, NO):
            continue
        p = periods[_government(r["data"])]
        p["votos"] += 1
        gov = r["orientacao_governo"]
        if gov in (YES, NO):
            p["com_orientacao_do_governo"] += 1
            p["votou_com_o_governo"] += r["voto"] == gov
        if r["maioria_do_partido"] in (YES, NO):
            p["votou_com_o_partido"] += r["voto"] == r["maioria_do_partido"]
        res = r["resultado"]
        if (res["maioria"] or 0) >= CONSENSUS_SHARE:
            winner = YES if res["sim"] >= res["nao"] else NO
            p["votacoes_consensuais"] += 1
            if r["voto"] != winner:
                p["contra_o_consenso"] += 1
                if gov in (YES, NO) and r["voto"] != gov:
                    p["contra_o_consenso_e_contra_o_governo"] += 1
    out = {}
    for name, p in periods.items():
        out[name] = {
            **p,
            "percentual_com_o_governo": ratio(p["votou_com_o_governo"], p["com_orientacao_do_governo"]),
            "percentual_com_o_partido": ratio(p["votou_com_o_partido"], p["votos"]),
        }
    return out


async def vote_records(parl: dict[str, Any], years: list[int]) -> list[dict[str, Any]]:
    """Every plenary merit roll call of `years` while in office, one record per vote, unabridged."""
    if parl["casa"] == "camara":
        return await _camara_records(parl["id"], years)
    return await _senado_records(parl["id"], years)


async def votes_by_axis(parl: dict[str, Any], years: list[int]) -> dict[str, Any]:
    records = await vote_records(parl, years)

    by_bill: dict[Any, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in records:
        by_bill[r["proposicao_id"]].append(r)
    items = [(_item(rs), rs[0]["eixos"]) for rs in by_bill.values()]

    # Each bill is listed once (with its axes); axes point to it by identifier.
    catalog: dict[str, dict[str, Any]] = {}
    axes = {}
    for key, label in EIXOS_AVALIACAO.items():
        mine = sorted((it for it, eixos in items if key in eixos), key=_salience)
        for it, eixos in items:
            if key in eixos and it in mine[:ITEMS_PER_AXIS]:
                catalog.setdefault(it["materia"], {**it, "eixos": eixos})
        axes[key] = {
            "eixo": label,
            "materias_votadas": len(mine),
            "votos_divergentes_do_partido": sum(1 for it in mine if it["votos_divergentes_do_partido"]),
            "ausencias": sum(1 for it in mine if it["voto"] == "ausente"),
            "destaques": [it["materia"] for it in mine[:ITEMS_PER_AXIS]],
        }
    unclassified = sorted((it for it, eixos in items if not eixos), key=_salience)
    for it in unclassified[:ITEMS_PER_AXIS]:
        catalog.setdefault(it["materia"], {**it, "eixos": []})

    return {
        "periodo": f"{years[0]}-{years[-1]}",
        "materias_com_votacao_nominal": len(items),
        "votacoes_de_merito": len(records),
        "por_eixo": axes,
        "sem_tema_oficial": {
            "quantidade": len(unclassified),
            "destaques": [it["materia"] for it in unclassified[:ITEMS_PER_AXIS]],
        },
        "materias": catalog,
        "posicionamento_por_governo": positioning_by_government(records),
        "ausencias_em_votacoes_disputadas": [
            {"materia": r["materia"], "data": r["data"], "votacao": r["descricao"][:120], "resultado": r["resultado"]}
            for r in records
            if r["voto"] == "ausente" and (r["resultado"]["maioria"] or 1) <= CONTESTED_SHARE
        ][:15],
    }
