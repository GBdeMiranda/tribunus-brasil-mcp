import asyncio
import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tribunus_brasil_mcp import activity
from tribunus_brasil_mcp.activity import (
    get_parliamentarian_activity,
    get_parliamentarian_votes,
)

DEPUTY = {"casa": "camara", "id": 160541, "nome": "Arthur Lira", "partido": "PP", "uf": "AL", "raw": {}}
SENATOR = {"casa": "senado", "id": 5894, "nome": "Flávio Bolsonaro", "partido": "PL", "uf": "RJ", "raw": {}}


def _write_csv(path, fields, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    return path


class TestActivity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        activity._camara_votacoes_cache.clear()
        activity._camara_votos_cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_deputy_activity_paginates_and_uses_period(self):
        seen_params = []

        async def fake_make_request(url, params=None):
            seen_params.append(dict(params))
            ev = {"id": 1, "descricaoTipo": "Sessão Deliberativa", "orgaos": [{"siglaOrgao": "PLEN"}]}
            if params["pagina"] == 1:
                return {"dados": [ev] * 100, "links": [{"rel": "next"}]}
            return {"dados": [dict(ev, descricaoTipo="Reunião de Comissão")] * 5, "links": []}

        with patch.object(activity, "resolve_parliamentarian", AsyncMock(return_value=DEPUTY)), \
             patch("tribunus_brasil_mcp.utils.make_request", side_effect=fake_make_request):
            res = asyncio.run(get_parliamentarian_activity("Arthur Lira", house="camara",
                                                           start_date="2025-01-01", end_date="2025-12-31", limit=10))

        self.assertEqual(res["total_eventos"], 105)
        self.assertEqual(res["eventos_por_tipo"], {"Sessão Deliberativa": 100, "Reunião de Comissão": 5})
        self.assertEqual(len(res["eventos"]), 10)
        self.assertEqual(seen_params[0]["dataInicio"], "2025-01-01")
        self.assertEqual(res["metadados"]["periodo"], "2025-01-01 a 2025-12-31")

    def test_senator_votes_classify_absences(self):
        def votacao(code, date, secret="N"):
            return {"identificacao": "PL 1/2025", "dataSessao": date, "votacaoSecreta": secret,
                    "votos": [{"codigoParlamentar": 5894, "siglaVotoParlamentar": code}]}

        payload = [
            votacao("Sim", "2025-03-01"), votacao("Não", "2025-03-02"), votacao("Votou", "2025-03-03", "S"),
            votacao("AP", "2025-03-04"), votacao("LS", "2025-03-05"), votacao("NCom", "2025-03-06"),
            votacao("P-NRV", "2025-03-07"), votacao("Presidente (art. 51 RISF)", "2025-03-08"),
        ]
        seen = {}

        async def fake_make_request(url, params=None):
            seen.update(params or {})
            return payload

        with patch.object(activity, "resolve_parliamentarian", AsyncMock(return_value=SENATOR)), \
             patch.object(activity, "make_request", side_effect=fake_make_request):
            res = asyncio.run(get_parliamentarian_votes("Flávio Bolsonaro", house="senado", year=2025, limit=3))

        p = res["participacao_plenario"]
        self.assertEqual(p["votacoes_nominais"], 8)
        self.assertEqual(p["votou"], 4)
        self.assertEqual(p["ausencia_justificada"], 2)
        self.assertEqual(p["ausencia_nao_justificada"], 1)
        self.assertEqual(p["presente_sem_votar"], 1)
        self.assertNotIn("taxa_participacao", p)
        self.assertEqual(seen["dataInicio"], "2025-01-01")
        self.assertEqual(res["votacoes"][0]["data"], "2025-03-08")
        self.assertEqual(len(res["votacoes"]), 3)

    def test_deputy_votes_participation_from_bulk_files(self):
        votacoes_path = _write_csv(
            Path(self.tmp.name) / "votacoes.csv",
            ["id", "siglaOrgao", "data", "dataHoraRegistro", "descricao"],
            [
                {"id": "v1", "siglaOrgao": "PLEN", "data": "2025-02-01", "dataHoraRegistro": "2025-02-01T10:00", "descricao": "Aprovado PL A"},
                {"id": "v2", "siglaOrgao": "PLEN", "data": "2025-02-02", "dataHoraRegistro": "2025-02-02T10:00", "descricao": "Aprovado PL B"},
                {"id": "v3", "siglaOrgao": "PLEN", "data": "2025-02-03", "dataHoraRegistro": "2025-02-03T10:00", "descricao": "Rejeitado PL C"},
                {"id": "c1", "siglaOrgao": "CCJC", "data": "2025-02-04", "dataHoraRegistro": "2025-02-04T10:00", "descricao": "Comissão"},
            ],
        )
        votos_path = _write_csv(
            Path(self.tmp.name) / "votos.csv",
            ["idVotacao", "dataHoraVoto", "voto", "deputado_id"],
            [
                {"idVotacao": "v1", "dataHoraVoto": "", "voto": "Sim", "deputado_id": "160541"},
                {"idVotacao": "v1", "dataHoraVoto": "", "voto": "Não", "deputado_id": "999"},
                {"idVotacao": "v2", "dataHoraVoto": "", "voto": "Não", "deputado_id": "999"},
                {"idVotacao": "v3", "dataHoraVoto": "", "voto": "Obstrução", "deputado_id": "160541"},
                {"idVotacao": "c1", "dataHoraVoto": "", "voto": "Sim", "deputado_id": "160541"},
            ],
        )

        async def fake_fetch(url, filename, max_age):
            return votos_path if "votacoesVotos" in filename else votacoes_path

        with patch.object(activity, "resolve_parliamentarian", AsyncMock(return_value=DEPUTY)), \
             patch.object(activity, "fetch_cached_file", side_effect=fake_fetch):
            res = asyncio.run(get_parliamentarian_votes("Arthur Lira", house="camara", year=2025))

        p = res["participacao_plenario"]
        self.assertEqual(p["votacoes_nominais"], 3)  # committee roll call excluded
        self.assertEqual(p["votou"], 2)
        self.assertEqual(p["sem_registro_de_voto"], 1)
        self.assertEqual(res["total_votacoes_encontradas"], 3)
        self.assertEqual(res["distribuicao_votos"], {"Sim": 2, "Obstrução": 1})
        self.assertEqual(res["votacoes"][0]["votacao_id"], "c1")
        self.assertTrue(res["votacoes"][0]["url"].endswith("/votacoes/c1"))

    def test_deputy_votes_bulk_unavailable(self):
        with patch.object(activity, "resolve_parliamentarian", AsyncMock(return_value=DEPUTY)), \
             patch.object(activity, "fetch_cached_file", AsyncMock(return_value=None)):
            res = asyncio.run(get_parliamentarian_votes("Arthur Lira", house="camara", year=2025))
        self.assertIn("error", res)


if __name__ == "__main__":
    unittest.main()
