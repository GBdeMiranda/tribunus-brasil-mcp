import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tribunus_brasil_mcp import staff
from tribunus_brasil_mcp.staff import office_staff

CAMARA = """﻿"grupo";"cargo";"lotacao";"dataInicioHistorico";"uriLotacao"
"Secretário Parlamentar";"SP01";"GAB. 1 - FULANA";"2023-02-01";"https://dadosabertos.camara.leg.br/api/v2/deputados/10"
"Secretário Parlamentar";"SP02";"GAB. 1 - FULANA";"2023-02-01";"https://dadosabertos.camara.leg.br/api/v2/deputados/10"
"Cargo de Natureza Especial";"CNE09";"GAB. 1 - FULANA";"2024-01-01";"https://dadosabertos.camara.leg.br/api/v2/deputados/10"
"Parlamentar";"Deputado";"GAB. 1 - FULANA";"2023-02-01";"https://dadosabertos.camara.leg.br/api/v2/deputados/10"
"Secretário Parlamentar";"SP01";"GAB. 2 - BELTRANO";"2023-02-01";"https://dadosabertos.camara.leg.br/api/v2/deputados/20"
"Quadro Efetivo - RJU";"Analista";"DETEC";"2010-01-01";""
"""
SENADO = [
    {"situacao": "ATIVO", "vinculo": "COMISSIONADO", "lotacao": {"nome": "Gabinete do Senador Flávio Arns"}, "ano_admissao": 2019},
    {"situacao": "ATIVO", "vinculo": "COMISSIONADO", "lotacao": {"nome": "Gabinete da Senadora Fulana de Tal"}, "ano_admissao": 2023},
    {"situacao": "ATIVO", "vinculo": "EFETIVO", "lotacao": {"nome": "Gabinete da Senadora Fulana de Tal"}, "ano_admissao": 2001},
    {"situacao": "ATIVO", "vinculo": "COMISSIONADO", "lotacao": {"nome": "Escritório de Apoio nº 1 da Senadora Fulana de Tal"}, "ano_admissao": 2023},
    {"situacao": "DESLIGADO", "vinculo": "COMISSIONADO", "lotacao": {"nome": "Gabinete da Senadora Fulana de Tal"}, "ano_admissao": 2020},
    {"situacao": "ATIVO", "vinculo": "PARLAMENTAR", "lotacao": {"nome": "Gabinete da Senadora Fulana de Tal"}, "ano_admissao": 2023},
]


class TestStaff(unittest.TestCase):
    def setUp(self):
        staff._staff_cache.clear()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "funcionarios.csv").write_text(CAMARA, encoding="utf-8")
        (base / "servidores.json").write_text(json.dumps(SENADO), encoding="utf-8")
        self.base = base

    def tearDown(self):
        self.tmp.cleanup()

    def staff_of(self, parl):
        async def fake_fetch(url, filename, max_age, **kwargs):
            return self.base / ("funcionarios.csv" if "camara" in url else "servidores.json")

        with patch.object(staff, "fetch_cached_file", side_effect=fake_fetch):
            return asyncio.run(office_staff(parl))

    def test_camara_office(self):
        res = self.staff_of({"casa": "camara", "id": 10, "nome": "Fulana"})
        self.assertEqual(res["quantidade"], 3)
        self.assertEqual(res["por_grupo"], {"Secretário Parlamentar": 2, "Cargo de Natureza Especial": 1})
        self.assertEqual(res["referencia_casa"], {"mediana": 2.0, "minimo": 1, "maximo": 3, "gabinetes": 2})

    def test_senado_office_by_name(self):
        res = self.staff_of({"casa": "senado", "id": 7, "nome": "Fulana de Tal"})
        self.assertEqual((res["quantidade"], res["gabinete_brasilia"], res["escritorios_no_estado"]), (3, 2, 1))
        self.assertEqual(res["por_vinculo"], {"COMISSIONADO": 2, "EFETIVO": 1})

    def test_senado_office_not_found(self):
        res = self.staff_of({"casa": "senado", "id": 8, "nome": "Ninguém"})
        self.assertIsNone(res["quantidade"])
        self.assertIn("não localizado", res["erro"])


if __name__ == "__main__":
    unittest.main()
