"""
Weekly routine in Brasília: the days each parliamentarian registered presence, set against the
days the house itself held deliberative sessions.

Câmara: presence lists of every plenary session and committee meeting (yearly files). Senado:
the attendance code of each nominal vote, the only presence record the Senate publishes.
"""

import asyncio
import collections
import datetime
from pathlib import Path
from typing import Any
from .activity import _classify_senate_vote
from .utils import cache_max_age, fetch_cached_file, iter_csv_rows, ratio
from .votes import CAMARA_FILES, SENADO_BASE_URL, _camara_status_timeline, _in_office, _senado_json

WEEKDAYS = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")

_camara_presence_cache: dict[tuple[int, float], dict[str, Any]] = {}


def _scan_camara_presence(events: Path, presence: Path) -> dict[str, Any]:
    """One pass over a year's files: deliberative session days and each deputy's presence by day."""
    kinds: dict[str, str] = {}
    session_days: set[str] = set()
    for r in iter_csv_rows(events):
        kind = r.get("descricaoTipo") or ""
        kinds[r.get("id") or ""] = "plenario" if kind.startswith("Sessão") else "comissao"
        if kind.startswith("Sessão Deliberativa") and (r.get("situacao") or "").startswith("Encerrada"):
            session_days.add((r.get("dataHoraInicio") or "")[:10])

    by_deputy: dict[str, dict[str, collections.Counter]] = collections.defaultdict(
        lambda: collections.defaultdict(collections.Counter)
    )
    for r in iter_csv_rows(presence):
        day = (r.get("dataHoraInicio") or "")[:10]
        by_deputy[r.get("idDeputado") or ""][day][kinds.get(r.get("idEvento") or "", "comissao")] += 1
    return {"session_days": session_days, "by_deputy": {d: dict(days) for d, days in by_deputy.items()}}


async def _camara_presence_year(year: int) -> dict[str, Any] | None:
    max_age = cache_max_age(year)
    paths = await asyncio.gather(*(
        fetch_cached_file(CAMARA_FILES.format(name=n, year=year), f"camara/{n}-{year}.csv", max_age)
        for n in ("eventos", "eventosPresencaDeputados")
    ))
    if not all(paths):
        return None
    key = (year, max(p.stat().st_mtime for p in paths))
    if key not in _camara_presence_cache:
        _camara_presence_cache[key] = await asyncio.to_thread(_scan_camara_presence, *paths)
    return _camara_presence_cache[key]


async def _camara_days(dep_id: int, years: list[int]) -> tuple[list[dict[str, Any]], bool]:
    loaded = await asyncio.gather(*(_camara_presence_year(y) for y in years))
    timeline = await _camara_status_timeline(dep_id)
    days: dict[str, dict[str, Any]] = {}
    for year in (y for y in loaded if y):
        own = year["by_deputy"].get(str(dep_id), {})
        for day in year["session_days"]:
            if _in_office(timeline, day):
                days[day] = {"data": day, "sessao": True, "presente": day in own}
        for day, counts in own.items():
            entry = days.setdefault(day, {"data": day, "sessao": day in year["session_days"], "presente": True})
            entry.update(plenario=counts.get("plenario", 0), comissoes=counts.get("comissao", 0))
    return sorted(days.values(), key=lambda d: d["data"]), all(loaded)


async def _senado_days(code: int, years: list[int]) -> tuple[list[dict[str, Any]], bool]:
    per_year = await asyncio.gather(*(
        _senado_json(f"{SENADO_BASE_URL}/votacao", f"senado/votacoes-senador-{code}-{y}.json", cache_max_age(y),
                     params={"codigoParlamentar": code, "dataInicio": f"{y}-01-01", "dataFim": f"{y}-12-31"})
        for y in years
    ))
    by_day: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for data in per_year:
        for v in data or []:
            if not isinstance(v, dict) or not v.get("dataSessao"):
                continue
            mine = next((x for x in v.get("votos", []) if str(x.get("codigoParlamentar")) == str(code)), {})
            by_day[v["dataSessao"][:10]][_classify_senate_vote(mine.get("siglaVotoParlamentar") or "")] += 1

    days = []
    for day, codes in sorted(by_day.items()):
        present = bool(codes["votou"] or codes["presente_sem_votar"])
        absence = None
        if not present:
            absence = "justificada" if codes["ausencia_justificada"] else "nao_justificada" if codes["ausencia_nao_justificada"] else None
        days.append({"data": day, "sessao": True, "presente": present, "votacoes": sum(codes.values()), "ausencia": absence})
    return days, all(d is not None for d in per_year)


def _summary(days: list[dict[str, Any]]) -> dict[str, Any]:
    weeks: dict[tuple[int, int], collections.Counter] = collections.defaultdict(collections.Counter)
    weekdays: collections.Counter = collections.Counter()
    for d in days:
        date = datetime.date.fromisoformat(d["data"])
        week = weeks[date.isocalendar()[:2]]
        week["sessao"] += d["sessao"]
        week["presente"] += d["presente"]
        if d["presente"]:
            weekdays[WEEKDAYS[date.weekday()]] += 1

    # Recess weeks would drag every average down alike; the base is the weeks the house worked.
    working = [w for w in weeks.values() if w["sessao"]]
    session_days = [d for d in days if d["sessao"]]
    return {
        "semanas_com_sessao": len(working),
        "media_dias_com_presenca_por_semana": round(sum(w["presente"] for w in working) / len(working), 2) if working else None,
        "media_dias_de_sessao_por_semana": round(sum(w["sessao"] for w in working) / len(working), 2) if working else None,
        "dias_com_presenca": sum(1 for d in days if d["presente"]),
        "dias_de_sessao": len(session_days),
        "presente_em_dias_de_sessao": sum(1 for d in session_days if d["presente"]),
        "percentual_dias_de_sessao": ratio(sum(1 for d in session_days if d["presente"]), len(session_days)),
        "por_dia_da_semana": {day: weekdays.get(day, 0) for day in WEEKDAYS},
    }


async def weekly_routine(parl: dict[str, Any], years: list[int]) -> dict[str, Any]:
    """
    Days with registered presence in Brasília, per week and weekday, against the house's own calendar.

    Câmara: any plenary session or committee meeting counts as presence; the house calendar is the
    days with a deliberative plenary session while the deputy was in office. Senado: the calendar is
    the days with a nominal vote, and presence is any vote or 'present, not voting' code.
    """
    if parl["casa"] == "camara":
        days, complete = await _camara_days(parl["id"], years)
        source = [CAMARA_FILES.format(name=n, year="{ano}") for n in ("eventosPresencaDeputados", "eventos")]
        limits = [
            "Conta a presença registrada em Brasília, em sessões do Plenário e reuniões de comissão; agenda no estado e trabalho de gabinete não aparecem.",
            "A Câmara não publica o motivo da ausência (licença, missão oficial ou falta).",
        ]
    else:
        days, complete = await _senado_days(parl["id"], years)
        source = [f"{SENADO_BASE_URL}/votacao?codigoParlamentar={parl['id']}"]
        limits = [
            "O Senado publica presença só nas votações nominais; dias de sessão sem votação nominal e reuniões de comissão não aparecem.",
        ]
    absences = None
    if parl["casa"] == "senado":
        absences = collections.Counter(d["ausencia"] for d in days if d.get("ausencia"))
    return {
        "casa": parl["casa"],
        "periodo": f"{years[0]}-{years[-1]}",
        **_summary(days),
        "ausencias": {"justificada": absences.get("justificada", 0), "nao_justificada": absences.get("nao_justificada", 0)} if absences is not None else None,
        "eventos": {
            "plenario": sum(d.get("plenario", 0) for d in days),
            "comissoes": sum(d.get("comissoes", 0) for d in days),
        } if parl["casa"] == "camara" else None,
        "dias": days,
        "metadados": {"fontes": source, "completo": complete, "limitacoes": limits},
    }
