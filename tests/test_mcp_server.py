import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tribunus_brasil_mcp.mcp_server import (
    _find_document_url,
    getParliamentarianProfile,
    getParliamentarianExpenses,
    getParliamentarianActivity,
    getParliamentarianVotes,
    evaluateParliamentarian,
    searchCongressionalThemes,
    searchLegislativeProposals,
    getBillText,
)
from tribunus_brasil_mcp.utils import extract_text_from_pdf


class TestMcpBrlCongressUnit(unittest.TestCase):
    def test_find_document_url(self):
        sample_list = [
            {"identificacao": "PL 2630/2020", "urlDocumento": "https://legis.senado.gov.br/doc1.pdf"},
            {"identificacao": "REQ 1/2020", "urlDocumento": "https://legis.senado.gov.br/doc2.pdf"},
        ]
        self.assertEqual(_find_document_url(sample_list), "https://legis.senado.gov.br/doc1.pdf")
        self.assertEqual(_find_document_url({"urlDocumento": "https://legis.senado.gov.br/doc.pdf"}), "https://legis.senado.gov.br/doc.pdf")
        self.assertIsNone(_find_document_url([]))

    def test_extract_text_from_pdf_mock(self):
        with patch("pymupdf.open") as mock_open:
            mock_doc = mock_open.return_value.__enter__.return_value
            mock_page = MagicMock()
            mock_page.get_text.return_value = "Texto simulado do PDF."
            mock_doc.__iter__.return_value = [mock_page]

            extracted = extract_text_from_pdf(b"dummy_bytes")
            self.assertEqual(extracted, "Texto simulado do PDF.")

    def test_extract_text_from_pdf_mock_truncation(self):
        with patch("pymupdf.open") as mock_open:
            mock_doc = mock_open.return_value.__enter__.return_value
            mock_page = MagicMock()
            mock_page.get_text.return_value = "A" * 2000
            mock_doc.__iter__.return_value = [mock_page]

            extracted = extract_text_from_pdf(b"dummy_bytes", max_chars=1000)
            self.assertEqual(len(extracted), 1000)

    def test_search_legislative_proposals_mock(self):
        mock_response = {
            "PesquisaBasicaMateria": {
                "Materias": {
                    "Materia": [
                        {
                            "Codigo": "141944",
                            "DescricaoIdentificacao": "PL 2630/2020",
                            "Autor": "Senador Alessandro Vieira",
                            "Ementa": "Institui a Lei Brasileira de Liberdade...",
                        }
                    ]
                }
            }
        }

        with patch("tribunus_brasil_mcp.mcp_server.make_request", new_callable=AsyncMock) as mock_make:
            mock_make.return_value = mock_response

            result_json = asyncio.run(searchLegislativeProposals(keyword="liberdade", year=2020))
            result = json.loads(result_json)

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["identificacao"], "PL 2630/2020")
            self.assertEqual(result[0]["autor"], "Senador Alessandro Vieira")

    def test_get_bill_text_mock(self):
        mock_process = [
            {
                "codigo": "141944",
                "identificacao": "PL 2630/2020",
                "ementa": "Institui a Lei...",
                "urlDocumento": "https://legis.senado.gov.br/doc1.pdf",
            }
        ]

        with patch("tribunus_brasil_mcp.mcp_server.make_request", new_callable=AsyncMock) as mock_make, \
             patch("tribunus_brasil_mcp.mcp_server.fetch_document_content", new_callable=AsyncMock) as mock_fetch, \
             patch("tribunus_brasil_mcp.mcp_server.extract_text_from_pdf") as mock_extract:

            mock_make.return_value = mock_process
            mock_fetch.return_value = b"pdf_data"
            mock_extract.return_value = "Texto simulado da lei"

            result_json = asyncio.run(getBillText("2630", "2020"))
            result = json.loads(result_json)

            self.assertEqual(mock_fetch.call_count, 1)
            self.assertEqual(result["identificacao"], "PL 2630/2020")
            self.assertEqual(result["texto"], "Texto simulado da lei")


    def test_get_parliamentarian_profile_deputy_mock(self):
        mock_search = {
            "dados": [
                {
                    "id": 160541,
                    "nome": "Arthur Lira",
                    "siglaPartido": "PP",
                    "siglaUf": "AL",
                    "email": "dep.arthurlira@camara.leg.br",
                    "urlFoto": "https://camara.leg.br/foto.jpg",
                }
            ]
        }
        mock_detail = {
            "dados": {
                "nomeCivil": "ARTHUR CÉSAR PEREIRA DE LIRA",
                "ultimoStatus": {
                    "siglaPartido": "PP",
                    "siglaUf": "AL",
                    "situacao": "Exercício",
                }
            }
        }

        async def fake_make_request(url, params=None):
            if "deputados?nome=" in url:
                return mock_search
            if "deputados/160541/orgaos" in url:
                return {"dados": [{"nomeOrgao": "Comissão X", "siglaOrgao": "CX", "titulo": "Titular"}]}
            if "proposicoes?idDeputadoAutor=" in url:
                return {"dados": [{"siglaTipo": "PL", "numero": "100", "ano": "2024", "ementa": "Ementa PL"}]}
            if "deputados/160541" in url:
                return mock_detail
            return {}

        with patch("tribunus_brasil_mcp.mcp_server.make_request", side_effect=fake_make_request), \
             patch("tribunus_brasil_mcp.identity.make_request", side_effect=fake_make_request):
            result_json = asyncio.run(getParliamentarianProfile("Arthur Lira", house="camara"))
            result = json.loads(result_json)

            self.assertEqual(result["parlamentar"], "Arthur Lira")
            self.assertEqual(result["nome_civil"], "ARTHUR CÉSAR PEREIRA DE LIRA")
            self.assertEqual(result["casa"], "Câmara dos Deputados")
            self.assertEqual(result["partido"], "PP")
            self.assertEqual(len(result["comissoes"]), 1)
            self.assertEqual(len(result["proposicoes_autoria"]), 1)
            self.assertEqual(result["proposicoes_autoria"][0]["identificacao"], "PL 100/2024")

    def test_get_parliamentarian_expenses_tool_mock(self):
        with patch("tribunus_brasil_mcp.mcp_server.get_parliamentarian_expenses", new_callable=AsyncMock) as mock_exp:
            mock_exp.return_value = {
                "parlamentar": "Arthur Lira",
                "casa": "Câmara dos Deputados",
                "total_despesas": 15000.0,
            }
            res_json = asyncio.run(getParliamentarianExpenses("Arthur Lira", house="camara"))
            res = json.loads(res_json)
            self.assertEqual(res["total_despesas"], 15000.0)

    def test_get_parliamentarian_activity_tool_mock(self):
        with patch("tribunus_brasil_mcp.mcp_server.get_parliamentarian_activity", new_callable=AsyncMock) as mock_act:
            mock_act.return_value = {
                "parlamentar": "Arthur Lira",
                "total_eventos": 5,
            }
            res_json = asyncio.run(getParliamentarianActivity("Arthur Lira", house="camara"))
            res = json.loads(res_json)
            self.assertEqual(res["total_eventos"], 5)

    def test_get_parliamentarian_votes_tool_mock(self):
        with patch("tribunus_brasil_mcp.mcp_server.get_parliamentarian_votes", new_callable=AsyncMock) as mock_vot:
            mock_vot.return_value = {
                "parlamentar": "Flávio Bolsonaro",
                "total_votacoes_encontradas": 8,
            }
            res_json = asyncio.run(getParliamentarianVotes("Flávio Bolsonaro", house="senado"))
            res = json.loads(res_json)
            self.assertEqual(res["total_votacoes_encontradas"], 8)

    def test_evaluate_parliamentarian_tool_mock(self):
        with patch("tribunus_brasil_mcp.mcp_server.evaluate_parliamentarian", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = {
                "parlamentar": "Flávio Bolsonaro",
                "avaliacao_qualitativa": {"parecer_sintese": "Atuação regular."},
            }
            res_json = asyncio.run(evaluateParliamentarian("Flávio Bolsonaro", house="senado"))
            res = json.loads(res_json)
            self.assertEqual(res["parlamentar"], "Flávio Bolsonaro")
            self.assertIn("avaliacao_qualitativa", res)

    def test_search_congressional_themes_tool_mock(self):
        with patch("tribunus_brasil_mcp.mcp_server.search_congressional_themes", new_callable=AsyncMock) as mock_theme:
            mock_theme.return_value = {
                "tema": "inteligência artificial",
                "resumo_executivo": "Atividade legislativa em andamento.",
                "metricas": {"total_proposicoes": 5},
            }
            res_json = asyncio.run(searchCongressionalThemes("inteligência artificial", house="auto"))
            res = json.loads(res_json)
            self.assertEqual(res["tema"], "inteligência artificial")
            self.assertIn("resumo_executivo", res)


class TestMcpBrlCongressLive(unittest.TestCase):
    """Live API integration tests against Brazilian Senate and Chamber open data endpoints."""

    @classmethod
    def setUpClass(cls):
        import urllib.request
        try:
            req = urllib.request.Request("https://dadosabertos.camara.leg.br/api/v2/deputados?itens=1", headers={"User-Agent": "test"})
            with urllib.request.urlopen(req, timeout=3):
                cls.network_available = True
        except Exception:
            cls.network_available = False

    def setUp(self):
        if not self.network_available:
            self.skipTest("External network access unavailable or restricted in test sandbox.")

    def test_live_search_legislative_proposals(self):
        result_json = asyncio.run(searchLegislativeProposals(keyword="inteligência artificial", limit=3))
        result = json.loads(result_json)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertLessEqual(len(result), 3)
        for m in result:
            self.assertIn("identificacao", m)
            self.assertIn("ementa", m)

    def test_live_get_bill_text(self):
        result_json = asyncio.run(getBillText(number="2630", year="2020"))
        result = json.loads(result_json)
        self.assertIn("identificacao", result)
        self.assertIn("PL 2630/2020", result["identificacao"])
        self.assertIn("ementa", result)
        self.assertIn("texto", result)
        self.assertTrue(len(result["texto"]) > 0)

    def test_live_get_parliamentarian_senator(self):
        result_json = asyncio.run(getParliamentarianProfile(name="Alan Rick", house="auto"))
        result = json.loads(result_json)
        self.assertEqual(result.get("casa"), "Senado Federal")
        self.assertEqual(result.get("parlamentar"), "Alan Rick")
        self.assertEqual(result.get("partido"), "REPUBLICANOS")
        self.assertIsInstance(result.get("comissoes"), list)
        self.assertIsInstance(result.get("proposicoes_autoria"), list)

    def test_live_get_parliamentarian_deputy(self):
        result_json = asyncio.run(getParliamentarianProfile(name="Arthur Lira", house="auto"))
        result = json.loads(result_json)
        self.assertEqual(result.get("casa"), "Câmara dos Deputados")
        self.assertEqual(result.get("parlamentar"), "Arthur Lira")
        self.assertEqual(result.get("partido"), "PP")
        self.assertEqual(result.get("uf"), "AL")
        self.assertIsInstance(result.get("comissoes"), list)
        self.assertIsInstance(result.get("proposicoes_autoria"), list)

    def test_live_evaluate_parliamentarian(self):
        result_json = asyncio.run(evaluateParliamentarian(name="Arthur Lira", house="camara"))
        result = json.loads(result_json)
        self.assertEqual(result.get("casa"), "Câmara dos Deputados")
        self.assertIn("avaliacao_qualitativa", result)
        self.assertIn("resumo_despesas", result)


if __name__ == "__main__":
    unittest.main()
