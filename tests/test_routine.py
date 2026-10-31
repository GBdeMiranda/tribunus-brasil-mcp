import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tribunus_brasil_mcp import routine
from tribunus_brasil_mcp.routine import weekly_routine

EVENTS = """﻿"id";"dataHoraInicio";"situacao";"descricaoTipo"
"1";"2025-03-04T14:00:00";"Encerrada";"Sessão Deliberativa"
"2";"2025-03-05T14:00:00";"Encerrada";"Sessão Deliberativa"
"3";"2025-03-05T10:00:00";"Encerrada";"Reunião Deliberativa"
"4";"2025-03-11T14:00:00";"Encerrada";"Sessão Deliberativa"
"5";"2025-03-12T14:00:00";"Cancelada";"Sessão Deliberativa"
"6";"2025-03-17T10:00:00";"Encerrada";"Audiência Pública"
"""
PRESENCE = """﻿"idEvento";"dataHoraInicio";"idDeputado"
"1";"2025-03-04T14:00:00";"10"
"2";"2025-03-05T14:00:00";"10"
"3";"2025-03-05T10:00:00";"10"
"6";"2025-03-17T10:00:00";"10"
"1";"2025-03-04T14:00:00";"20"
"""
TIMELINE = [{"dataHora": "2023-02-01T00:00", "idLegislatura": 57, "situacao": "Exercício"}]


class TestRoutine(unittest.TestCase):
    def setUp(self):
        routine._camara_presence_cache.clear()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.files = {"eventos": base / "eventos.csv", "eventosPresencaDeputados": base / "presenca.csv"}
        self.files["eventos"].write_text(EVENTS, encoding="utf-8")
        self.files["eventosPresencaDeputados"].write_text(PRESENCE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_camara(self, dep_id):
        async def fake_fetch(url, filename, max_age, **kwargs):
            return self.files["eventosPresencaDeputados" if "Presenca" in url else "eventos"]

        with patch.object(routine, "fetch_cached_file", side_effect=fake_fetch), \
             patch.object(routine, "_camara_status_timeline", AsyncMock(return_value=TIMELINE)):
            return asyncio.run(weekly_routine({"casa": "camara", "id": dep_id}, [2025]))

    def test_camara_weeks_and_presence(self):
        r = self.run_camara(10)
        # Weeks with a held deliberative session: 10/2025 (2 days) and 11/2025 (1 day, the other was cancelled).
        self.assertEqual(r["semanas_com_sessao"], 2)
        self.assertEqual(r["media_dias_de_sessao_por_semana"], 1.5)
        self.assertEqual(r["media_dias_com_presenca_por_semana"], 1.0)
        self.assertEqual((r["dias_de_sessao"], r["presente_em_dias_de_sessao"], r["dias_com_presenca"]), (3, 2, 3))
        self.assertEqual(r["eventos"], {"plenario": 2, "comissoes": 2})
        self.assertEqual(r["por_dia_da_semana"]["segunda"], 1)

    def test_camara_absent_deputy(self):
        r = self.run_camara(20)
        self.assertEqual((r["dias_de_sessao"], r["presente_em_dias_de_sessao"]), (3, 1))

    def test_senado_presence_from_vote_codes(self):
        votes = [
            {"dataSessao": "2025-03-04", "votos": [{"codigoParlamentar": 7, "siglaVotoParlamentar": "Sim"}]},
            {"dataSessao": "2025-03-05", "votos": [{"codigoParlamentar": 7, "siglaVotoParlamentar": "MIS"}]},
            {"dataSessao": "2025-03-11", "votos": [{"codigoParlamentar": 7, "siglaVotoParlamentar": "NCom"}]},
            {"dataSessao": "2025-03-11", "votos": [{"codigoParlamentar": 7, "siglaVotoParlamentar": "P-NRV"}]},
        ]
        with patch.object(routine, "_senado_json", AsyncMock(return_value=votes)):
            r = asyncio.run(weekly_routine({"casa": "senado", "id": 7}, [2025]))
        self.assertEqual((r["dias_de_sessao"], r["dias_com_presenca"]), (3, 2))
        self.assertEqual(r["ausencias"], {"justificada": 1, "nao_justificada": 0})
        self.assertIsNone(r["eventos"])


if __name__ == "__main__":
    unittest.main()
