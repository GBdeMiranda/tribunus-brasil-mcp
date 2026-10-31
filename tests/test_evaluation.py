import asyncio
import datetime
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tribunus_brasil_mcp import evaluation
from tribunus_brasil_mcp.evaluation import (
    _is_local_government,
    _legislature_start,
    public_fund,
    classify_front,
    evaluate_parliamentarian,
)
from tribunus_brasil_mcp.votes import EIXOS_AVALIACAO, _axes_for_camara, _axes_for_senado

DEPUTY = {"casa": "camara", "id": 152605, "nome": "Glauber Braga", "nome_civil": None, "partido": "PSOL", "uf": "RJ", "raw": {}}


class TestHelpers(unittest.TestCase):
    def test_classify_front(self):
        self.assertEqual(classify_front("Frente Parlamentar da Agropecuária - FPA"), ["agronegocio"])
        self.assertIn("mercado_e_empresariado", classify_front("Frente Parlamentar Mista do Ambiente de Negócios (FPN)"))
        self.assertIn("religiao_e_costumes", classify_front("Frente Parlamentar Evangélica do Congresso Nacional"))
        self.assertEqual(classify_front("Frente Parlamentar em Defesa do SUS"), ["saude_e_educacao_publicas"])
        self.assertEqual(classify_front("Frente Parlamentar Mista em Defesa dos Serviços de Saúde"), ["saude_e_educacao_publicas"])
        self.assertNotIn("agronegocio", classify_front("Frente Parlamentar Mista da Agricultura Familiar"))
        self.assertEqual(classify_front("Frente Parlamentar de Apoio ao Microcrédito e às Microfinanças"), ["setor_financeiro"])
        # 'sus' must not match inside 'sustentável'
        self.assertNotIn("saude_e_educacao_publicas", classify_front("Frente Parlamentar do Turismo Sustentável"))

    def test_local_government(self):
        self.assertTrue(_is_local_government("Município"))
        self.assertTrue(_is_local_government("Fundo Público da Administração Direta Municipal"))
        self.assertTrue(_is_local_government("Estado ou Distrito Federal"))
        self.assertFalse(_is_local_government("Sociedade de Economia Mista"))
        self.assertFalse(_is_local_government(None))

    def test_legislature_start(self):
        self.assertEqual(_legislature_start(datetime.date(2026, 9, 22)), datetime.date(2023, 2, 1))
        self.assertEqual(_legislature_start(datetime.date(2027, 1, 15)), datetime.date(2023, 2, 1))
        self.assertEqual(_legislature_start(datetime.date(2027, 2, 2)), datetime.date(2027, 2, 1))

    def test_axes_classification(self):
        self.assertEqual(
            set(EIXOS_AVALIACAO),
            {
                "trabalho_renda_e_previdencia",
                "servicos_publicos_e_bem_estar",
                "patrimonio_publico_e_soberania",
                "institucional_e_administracao_publica",
                "transparencia_e_controle_social",
            },
        )
        self.assertEqual(_axes_for_camara(["Trabalho e Emprego"]), ["trabalho_renda_e_previdencia"])
        self.assertEqual(_axes_for_camara(["Saúde"]), ["servicos_publicos_e_bem_estar"])
        self.assertEqual(
            _axes_for_senado(["Política Social / Previdência Social"], "Dispõe sobre transparência de emendas parlamentares"),
            ["trabalho_renda_e_previdencia", "transparencia_e_controle_social"],
        )


class TestPublicFund(unittest.TestCase):
    def test_territory_uses_only_states_and_municipalities(self):
        payments = [
            {"grupo": "ente_ou_entidade_publica", "natureza_juridica": "Município", "municipio": "NITERÓI", "uf": "RJ",
             "valor": 900.0, "favorecido": "MUNICIPIO", "documento_favorecido": "1"},
            {"grupo": "ente_ou_entidade_publica", "natureza_juridica": "Município", "municipio": "VITÓRIA", "uf": "ES",
             "valor": 100.0, "favorecido": "MUNICIPIO ES", "documento_favorecido": "2"},
            {"grupo": "agente_financeiro", "natureza_juridica": "Sociedade de Economia Mista", "municipio": "BRASÍLIA",
             "uf": "DF", "valor": 5000.0, "favorecido": "BANCO DO BRASIL SA", "documento_favorecido": "3"},
            {"grupo": "empresa_privada", "natureza_juridica": "Sociedade Empresária Limitada", "municipio": "SÃO PAULO",
             "uf": "SP", "valor": 300.0, "favorecido": "FORNECEDORA", "documento_favorecido": "4"},
        ]
        amendments = {
            "localizado": True,
            "emendas": [{"ano": 2025, "tipo": "Emenda Individual - Transferências Especiais", "funcao": "Saúde",
                         "empenhado": 1000.0, "pago": 1000.0}],
            "pagamentos": payments,
            "referencia_autores": {2025: {"n_autores": 1}},
            "metadados": {"fonte": "x"},
        }
        with patch.object(evaluation, "get_amendments", AsyncMock(return_value=amendments)):
            res = asyncio.run(public_fund(DEPUTY, [2025]))

        territory = res["concentracao_territorial"]
        self.assertEqual(territory["recebido_por_estados_e_municipios"], 1000.0)
        self.assertEqual(territory["fora_da_uf_do_mandato"], {"valor": 100.0, "percentual": 0.1})
        self.assertEqual(res["destino_dos_pagamentos"]["por_tipo_de_favorecido"]["agente_financeiro"]["valor"], 5000.0)
        self.assertEqual(res["maiores_favorecidos_privados"][0]["favorecido"], "FORNECEDORA")
        self.assertEqual(res["emendas_individuais_por_ano"][2025]["transferencias_especiais_empenhado"], 1000.0)


class TestEvaluateParliamentarian(unittest.TestCase):
    def test_returns_five_dimensions_and_instruction(self):
        with patch.object(evaluation, "resolve_parliamentarian", AsyncMock(return_value=DEPUTY)), \
             patch.object(evaluation, "_birthplace", AsyncMock(return_value="Nova Friburgo/RJ")), \
             patch.object(evaluation, "votes_by_axis", AsyncMock(return_value={"periodo": "2023-2026", "por_eixo": {}})), \
             patch.object(evaluation, "_social_base", AsyncMock(return_value={"comissoes_atuais": []})), \
             patch.object(evaluation, "public_fund", AsyncMock(return_value={"localizado": False})), \
             patch.object(evaluation, "_publicity", AsyncMock(return_value={"ano_referencia": 2025})), \
             patch.object(evaluation, "_agenda_and_discourse", AsyncMock(return_value={"proposicoes": {}, "discursos": {}})):
            res = asyncio.run(evaluate_parliamentarian("Glauber Braga", house="camara", year=2025))

        self.assertEqual(res["parlamentar"]["nome"], "Glauber Braga")
        self.assertEqual(res["parlamentar"]["naturalidade"], "Nova Friburgo/RJ")
        self.assertEqual(list(res["dimensoes"]),
                         ["votacoes_por_eixo", "base_social", "fundo_publico", "publicidade", "agenda_e_discurso"])
        self.assertEqual(res["periodo"]["votacoes"], "2023-2026")
        self.assertIn("Glauber Braga (Câmara dos Deputados, PSOL/RJ)", res["instrucao"])
        for label in EIXOS_AVALIACAO.values():
            self.assertIn(label, res["instrucao"])
        self.assertIn("primado do trabalho", res["instrucao"])
        self.assertIn("Quem ganha e quem paga", res["instrucao"])
        self.assertIn("frações do capital", res["instrucao"])
        self.assertIn("Autobenefício e clientela", res["instrucao"])
        self.assertIn("CF, art. 37", res["instrucao"])
        self.assertIn("o juízo recai sobre conteúdo, beneficiários e coerência", res["instrucao"])

    def test_ambiguous_name_is_returned(self):
        ambiguous = {"error": "ambiguous", "candidatos": []}
        with patch.object(evaluation, "resolve_parliamentarian", AsyncMock(return_value=ambiguous)):
            self.assertEqual(asyncio.run(evaluate_parliamentarian("Lira")), ambiguous)


if __name__ == "__main__":
    unittest.main()
