import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tribunus_brasil_mcp import votes
from tribunus_brasil_mcp.votes import positioning_by_government, vote_records


def record(data, voto, governo, partido, sim, nao):
    return {"data": data, "voto": voto, "orientacao_governo": governo, "maioria_do_partido": partido,
            "resultado": {"sim": sim, "nao": nao, "abstencao": 0, "maioria": max(sim, nao) / (sim + nao)}}


class TestVotes(unittest.TestCase):
    def test_vote_records_dispatches_by_house(self):
        with patch.object(votes, "_camara_records", AsyncMock(return_value=["c"])) as camara, \
             patch.object(votes, "_senado_records", AsyncMock(return_value=["s"])) as senado:
            self.assertEqual(asyncio.run(vote_records({"casa": "camara", "id": 1}, [2025])), ["c"])
            self.assertEqual(asyncio.run(vote_records({"casa": "senado", "id": 2}, [2025])), ["s"])
        camara.assert_awaited_once_with(1, [2025])
        senado.assert_awaited_once_with(2, [2025])

    def test_positioning_by_government(self):
        records = [
            record("2024-03-01", "Sim", "Sim", "Sim", 400, 20),   # consensual, with everyone
            record("2024-04-01", "Não", "Sim", "Sim", 450, 10),   # against consensus and government
            record("2024-05-01", "ausente", "Sim", "Sim", 250, 200),
            record("2021-05-01", "Sim", "Não", "Sim", 300, 150),
        ]
        out = positioning_by_government(records)
        self.assertEqual(set(out), {"Lula", "Bolsonaro"})
        lula = out["Lula"]
        self.assertEqual(lula["votos"], 2)
        self.assertEqual(lula["votou_com_o_governo"], 1)
        self.assertEqual(lula["contra_o_consenso_e_contra_o_governo"], 1)
        self.assertEqual(lula["percentual_com_o_partido"], 0.5)
        self.assertEqual(out["Bolsonaro"]["votou_com_o_governo"], 0)


if __name__ == "__main__":
    unittest.main()
