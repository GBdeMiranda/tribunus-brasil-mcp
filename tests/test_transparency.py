import asyncio
import csv
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tribunus_brasil_mcp import transparency
from tribunus_brasil_mcp.transparency import get_parliamentarian_expenses, _normalize_float
from tribunus_brasil_mcp.utils import fetch_all_pages

DEPUTY = {"casa": "camara", "id": 160541, "nome": "Arthur Lira", "partido": "PP", "uf": "AL", "raw": {}}
SENATOR = {"casa": "senado", "id": 4994, "nome": "Eduardo Braga", "partido": "MDB", "uf": "AM", "raw": {}}

CEAP_FIELDS = ["txNomeParlamentar", "ideCadastro", "txtDescricao", "txtFornecedor", "txtCNPJCPF",
               "txtNumero", "datEmissao", "vlrLiquido", "numMes", "numAno", "urlDocumento"]


def _ceap_row(dep_id, value, supplier="LATAM", cnpj="02012862000160", url="https://camara.leg.br/d.pdf", month=5):
    return {"txNomeParlamentar": "X", "ideCadastro": str(dep_id), "txtDescricao": "PASSAGEM AÉREA",
            "txtFornecedor": supplier, "txtCNPJCPF": cnpj, "txtNumero": "1",
            "datEmissao": f"2025-{month:02d}-10T00:00:00", "vlrLiquido": value, "numMes": str(month),
            "numAno": "2025", "urlDocumento": url}


class TestTransparency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        transparency._ceap_rows_cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_ceap_zip(self, rows):
        csv_path = Path(self.tmp.name) / "Ano-2025.csv"
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CEAP_FIELDS, delimiter=";", quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(rows)
        zip_path = Path(self.tmp.name) / "ceap.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(csv_path, "Ano-2025.csv")
        return zip_path

    def test_normalize_float(self):
        self.assertEqual(_normalize_float(100), 100.0)
        self.assertEqual(_normalize_float("150.50"), 150.50)
        self.assertEqual(_normalize_float("1.250,50"), 1250.50)
        self.assertEqual(_normalize_float(None), 0.0)
        self.assertEqual(_normalize_float("invalid"), 0.0)

    def test_deputy_expenses_use_complete_bulk_file(self):
        # 250 receipts: more than one API page (100), which the old code silently truncated.
        rows = [_ceap_row(160541, "100") for _ in range(249)]
        rows.append(_ceap_row(160541, "500.5", supplier="AGÊNCIA", cnpj="11222333000199", url="", month=6))
        rows.append(_ceap_row(123086, "99999"))  # homonym's receipt must not leak in
        zip_path = self._write_ceap_zip(rows)

        with patch.object(transparency, "resolve_parliamentarian", AsyncMock(return_value=DEPUTY)), \
             patch.object(transparency, "fetch_cached_file", AsyncMock(return_value=zip_path)):
            res = asyncio.run(get_parliamentarian_expenses("Arthur Lira", house="camara", year=2025, limit=3))

        self.assertEqual(res["quantidade_despesas"], 250)
        self.assertEqual(res["total_despesas"], 25400.50)
        self.assertEqual(res["despesas_sem_comprovante_digital"], {"quantidade": 1, "valor": 500.5})
        self.assertEqual(len(res["despesas"]), 3)
        self.assertEqual(res["despesas"][0]["mes"], 6)  # most recent first
        self.assertTrue(res["metadados"]["completo"])
        self.assertEqual(res["metadados"]["n_registros"], 250)

        with patch.object(transparency, "resolve_parliamentarian", AsyncMock(return_value=DEPUTY)), \
             patch.object(transparency, "fetch_cached_file", AsyncMock(return_value=zip_path)):
            res = asyncio.run(get_parliamentarian_expenses("Arthur Lira", house="camara", year=2025, month=6))
        self.assertEqual(res["quantidade_despesas"], 1)

    def test_deputy_expenses_fall_back_to_paginated_api(self):
        pages = {
            1: {"dados": [{"tipoDespesa": "A", "valorLiquido": 10, "nomeFornecedor": "F"}] * 100,
                "links": [{"rel": "next"}]},
            2: {"dados": [{"tipoDespesa": "A", "valorLiquido": 10, "nomeFornecedor": "F"}] * 20, "links": []},
        }

        async def fake_make_request(url, params=None):
            return pages[params["pagina"]]

        with patch.object(transparency, "resolve_parliamentarian", AsyncMock(return_value=DEPUTY)), \
             patch.object(transparency, "fetch_cached_file", AsyncMock(return_value=None)), \
             patch("tribunus_brasil_mcp.utils.make_request", side_effect=fake_make_request):
            res = asyncio.run(get_parliamentarian_expenses("Arthur Lira", house="camara", year=2025))

        self.assertEqual(res["quantidade_despesas"], 120)
        self.assertEqual(res["total_despesas"], 1200.0)
        self.assertTrue(res["metadados"]["completo"])

    def test_fetch_all_pages_reports_failure(self):
        async def fake_make_request(url, params=None):
            return {"dados": [1], "links": [{"rel": "next"}]} if params["pagina"] == 1 else None

        with patch("tribunus_brasil_mcp.utils.make_request", side_effect=fake_make_request):
            records, complete = asyncio.run(fetch_all_pages("http://x"))
        self.assertEqual(records, [1])
        self.assertFalse(complete)

    def test_senator_expenses_match_by_code_not_name(self):
        ceaps = [
            {"codSenador": 4994, "nomeSenador": "EDUARDO BRAGA", "ano": 2025, "mes": 4,
             "tipoDespesa": "Locação de imóveis", "fornecedor": "IMOBILIÁRIA", "cpfCnpj": "12345678000100",
             "valorReembolsado": 4500, "data": "2025-04-05", "documento": "REC456"},
            {"codSenador": 3777, "nomeSenador": "EDUARDO GOMES", "ano": 2025, "mes": 4,
             "tipoDespesa": "Locação de imóveis", "fornecedor": "OUTRA", "cpfCnpj": "1",
             "valorReembolsado": 9000, "data": "2025-04-06", "documento": "REC999"},
        ]
        json_path = Path(self.tmp.name) / "ceaps.json"
        json_path.write_text(__import__("json").dumps(ceaps), encoding="utf-8")

        with patch.object(transparency, "resolve_parliamentarian", AsyncMock(return_value=SENATOR)), \
             patch.object(transparency, "fetch_cached_file", AsyncMock(return_value=json_path)):
            res = asyncio.run(get_parliamentarian_expenses("Eduardo Braga", house="senado", year=2025))

        self.assertEqual(res["total_despesas"], 4500.00)
        self.assertEqual(res["principais_fornecedores"][0]["fornecedor"], "IMOBILIÁRIA")
        self.assertEqual(res["despesas"][0]["documento_numero"], "REC456")
        self.assertIsNone(res["despesas"][0]["documento_url"])
        self.assertNotIn("despesas_sem_comprovante_digital", res)

    def test_ambiguous_name_is_propagated(self):
        ambiguous = {"error": "ambiguous", "candidatos": [{"id": 1}, {"id": 2}]}
        with patch.object(transparency, "resolve_parliamentarian", AsyncMock(return_value=ambiguous)):
            res = asyncio.run(get_parliamentarian_expenses("Lira"))
        self.assertEqual(res, ambiguous)


if __name__ == "__main__":
    unittest.main()
