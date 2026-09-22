import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tribunus_brasil_mcp.themes import (
    search_congressional_themes,
    _generate_thematic_summary,
)


class TestThemes(unittest.TestCase):
    def test_empty_theme_validation(self):
        res = asyncio.run(search_congressional_themes(""))
        self.assertIn("error", res)

    def test_generate_thematic_summary_empty(self):
        summary = _generate_thematic_summary("clima", [], [], [], [])
        self.assertIn("Não foram encontradas deliberações", summary)

    def test_generate_thematic_summary_populated(self):
        bills = [{"identificacao": "PL 2630/2020"}]
        speeches = [{"parlamentar": "Senador X"}]
        hearings = [{"comissoes": ["CCT", "CCJC"]}]
        votes = [{"votacao_id": 123}]

        summary = _generate_thematic_summary("inteligência artificial", bills, speeches, hearings, votes)
        self.assertIn("inteligência artificial", summary)
        self.assertIn("PL 2630/2020", summary)
        self.assertIn("CCJC", summary)
        self.assertIn("CCT", summary)

    def test_search_congressional_themes_mock(self):
        mock_senate_bills = {
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
        mock_chamber_bills = {
            "dados": [
                {
                    "id": 220011,
                    "siglaTipo": "PL",
                    "numero": "21",
                    "ano": "2020",
                    "ementa": "Marco legal da IA.",
                }
            ]
        }
        mock_senate_speeches = {
            "ListaDiscursos": {
                "Discursos": {
                    "Discurso": [
                        {
                            "NomeParlamentar": "Senador Eduardo",
                            "SiglaPartidoParlamentar": "PL",
                            "DataDiscurso": "2024-03-10",
                            "TextoResumo": "Debate sobre regulação e inteligência artificial no Brasil.",
                        }
                    ]
                }
            }
        }
        mock_chamber_events = {
            "dados": [
                {
                    "descricaoTipo": "Audiência Pública",
                    "situacao": "Encerrada",
                    "dataHoraInicio": "2024-04-15T09:00",
                    "descricao": "Audiência sobre inteligência artificial e ética",
                    "orgaos": [{"siglaOrgao": "CCT"}],
                }
            ]
        }
        mock_chamber_votes = {
            "dados": [
                {
                    "id": "VOT-101",
                    "dataHoraRegistro": "2024-05-02T16:00",
                    "proposicaoObjeto": "PL 21/2020",
                    "descricao": "Votação do parecer sobre inteligência artificial",
                    "aprovacao": 1,
                }
            ]
        }

        async def fake_make_request(url, params=None):
            if "materia/pesquisa/lista" in url:
                return mock_senate_bills
            if "proposicoes" in url:
                return mock_chamber_bills
            if "plenario/lista/discursos" in url:
                return mock_senate_speeches
            if "eventos" in url:
                return mock_chamber_events
            if "votacoes" in url:
                return mock_chamber_votes
            return {}

        with patch("tribunus_brasil_mcp.themes.make_request", side_effect=fake_make_request):
            res = asyncio.run(search_congressional_themes("inteligência artificial", limit=3))

            self.assertEqual(res["tema"], "inteligência artificial")
            self.assertEqual(res["metricas"]["total_proposicoes"], 2)
            self.assertEqual(res["metricas"]["total_discursos"], 1)
            self.assertEqual(res["metricas"]["total_audiencias_comissoes"], 1)
            self.assertEqual(res["metricas"]["total_votacoes"], 1)
            self.assertIn("resumo_executivo", res)
            self.assertEqual(len(res["proposicoes"]), 2)
            self.assertEqual(res["proposicoes"][0]["casa"], "Senado Federal")
            self.assertEqual(res["proposicoes"][1]["casa"], "Câmara dos Deputados")


if __name__ == "__main__":
    unittest.main()
