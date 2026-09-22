import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tribunus_brasil_mcp.evaluation import (
    _extract_thematic_keywords,
    _generate_qualitative_assessment,
    evaluate_parliamentarian,
)


class TestEvaluation(unittest.TestCase):
    def test_extract_thematic_keywords(self):
        texts = [
            "Requer comissão para apurar desastres em mineração e segurança de barragens",
            "Projeto de lei que altera o código penal e combate ao crime organizado",
        ]
        themes = _extract_thematic_keywords(texts)
        self.assertIn("Meio Ambiente e Sustentabilidade", themes)
        self.assertIn("Segurança Pública e Defesa", themes)

    def test_generate_qualitative_assessment(self):
        profile = {
            "parlamentar": "Senador Exemplo",
            "casa": "Senado Federal",
            "partido": "ABC",
            "uf": "SP",
            "comissoes": [{"nome": "Comissão de Constituição e Justiça"}],
            "proposicoes_autoria": [{"ementa": "Altera o código tributário nacional"}],
        }
        expenses = {
            "ano_referencia": 2024,
            "total_despesas": 45000.00,
            "gastos_por_categoria": {"PASSAGENS AÉREAS": 30000.0, "COMBUSTÍVEL": 15000.0},
            "principais_fornecedores": [{"fornecedor": "FORNECEDOR X", "total": 30000.0}],
        }
        activity = {"total_discursos": 12}
        votes = {
            "total_votacoes_encontradas": 10,
            "distribuicao_votos": {"Sim": 8, "Não": 2},
        }
        themes = ["Economia, Tributação e Orçamento"]

        assessment = _generate_qualitative_assessment(profile, expenses, activity, votes, themes)
        self.assertIn("Senador Exemplo", assessment["foco_tematico"])
        self.assertIn("R$ 45,000.00", assessment["perfil_fiscal_e_transparencia"])
        self.assertIn("Sim: 8", assessment["engajamento_plenario"])
        self.assertIn("instrucoes_de_analise_mpp", assessment)

    def test_evaluate_parliamentarian_integration(self):
        mock_profile = {
            "parlamentar": "Flávio Bolsonaro",
            "nome_civil": "Flávio Nantes Bolsonaro",
            "casa": "Senado Federal",
            "partido": "PL",
            "uf": "RJ",
            "email": "sen.flaviobolsonaro@senado.leg.br",
            "foto": "http://foto.jpg",
            "comissoes": [{"nome": "Comissão de Segurança Pública"}],
            "proposicoes_autoria": [{"ementa": "Institui regras de segurança pública"}],
        }
        mock_expenses = {
            "parlamentar": "Flávio Bolsonaro",
            "casa": "Senado Federal",
            "ano_referencia": 2024,
            "total_despesas": 25000.0,
            "gastos_por_categoria": {"CONSULTORIAS": 25000.0},
            "principais_fornecedores": [{"fornecedor": "CONSULTORIA Y", "total": 25000.0}],
        }
        mock_activity = {
            "parlamentar": "Flávio Bolsonaro",
            "casa": "Senado Federal",
            "total_discursos": 10,
        }
        mock_votes = {
            "parlamentar": "Flávio Bolsonaro",
            "casa": "Senado Federal",
            "total_votacoes_encontradas": 5,
            "distribuicao_votos": {"Sim": 4, "Não": 1},
        }

        async def fake_profile(name, house="auto", includeVotes=True, limit=10):
            return mock_profile

        with patch("tribunus_brasil_mcp.evaluation.get_parliamentarian_expenses", new_callable=AsyncMock) as m_exp, \
             patch("tribunus_brasil_mcp.evaluation.get_parliamentarian_activity", new_callable=AsyncMock) as m_act, \
             patch("tribunus_brasil_mcp.evaluation.get_parliamentarian_votes", new_callable=AsyncMock) as m_vot:

            m_exp.return_value = mock_expenses
            m_act.return_value = mock_activity
            m_vot.return_value = mock_votes

            report = asyncio.run(evaluate_parliamentarian("Flávio Bolsonaro", profile_getter=fake_profile))

            self.assertEqual(report["parlamentar"], "Flávio Bolsonaro")
            self.assertEqual(report["partido"], "PL")
            self.assertEqual(report["resumo_despesas"]["total_despesas"], 25000.0)
            self.assertIn("avaliacao_qualitativa", report)
            self.assertIn("foco_tematico", report["avaliacao_qualitativa"])


if __name__ == "__main__":
    unittest.main()
