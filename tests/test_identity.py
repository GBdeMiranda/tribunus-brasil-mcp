import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from tribunus_brasil_mcp.identity import resolve_parliamentarian
from tribunus_brasil_mcp.utils import normalize_name

SENATORS = {
    "ListaParlamentarEmExercicio": {
        "Parlamentares": {
            "Parlamentar": [
                {"IdentificacaoParlamentar": {"CodigoParlamentar": "4994", "NomeParlamentar": "Eduardo Braga",
                                              "NomeCompletoParlamentar": "Carlos Eduardo de Souza Braga",
                                              "SiglaPartidoParlamentar": "MDB", "UfParlamentar": "AM"}},
                {"IdentificacaoParlamentar": {"CodigoParlamentar": "3777", "NomeParlamentar": "Eduardo Gomes",
                                              "NomeCompletoParlamentar": "Carlos Eduardo Torres Gomes",
                                              "SiglaPartidoParlamentar": "PL", "UfParlamentar": "TO"}},
            ]
        }
    }
}
DEPUTIES_LIRA = {"dados": [
    {"id": 160541, "nome": "Arthur Lira", "siglaPartido": "PP", "siglaUf": "AL"},
    {"id": 123086, "nome": "Átila Lira", "siglaPartido": "PP", "siglaUf": "PI"},
]}


async def fake_make_request(url, params=None):
    if "senador/lista/atual" in url:
        return SENATORS
    if "deputados?nome=" in url:
        return DEPUTIES_LIRA if "Lira" in url or "lira" in url else {"dados": []}
    if url.endswith("/deputados/160541"):
        return {"dados": {"id": 160541, "nomeCivil": "ARTHUR CÉSAR PEREIRA DE LIRA",
                          "ultimoStatus": {"nome": "Arthur Lira", "siglaPartido": "PP", "siglaUf": "AL"}}}
    return {}


def resolve(*args, **kwargs):
    with patch("tribunus_brasil_mcp.identity.make_request", side_effect=fake_make_request):
        return asyncio.run(resolve_parliamentarian(*args, **kwargs))


class TestIdentity(unittest.TestCase):
    def test_normalize_name(self):
        self.assertEqual(normalize_name("  Átila   LIRA "), "atila lira")

    def test_ambiguous_surname_returns_candidates(self):
        res = resolve("Lira", house="camara")
        self.assertIn("error", res)
        self.assertEqual({c["id"] for c in res["candidatos"]}, {160541, 123086})

    def test_exact_name_wins_over_partial_matches(self):
        res = resolve("Arthur Lira", house="auto")
        self.assertEqual(res["id"], 160541)
        self.assertEqual(res["casa"], "camara")

    def test_accent_insensitive_exact_match(self):
        res = resolve("atila lira", house="camara")
        self.assertEqual(res["id"], 123086)

    def test_senate_substring_collision_is_ambiguous(self):
        res = resolve("Eduardo", house="senado")
        self.assertEqual(len(res["candidatos"]), 2)

    def test_senate_unique_match(self):
        res = resolve("Braga", house="senado")
        self.assertEqual(res["id"], 4994)
        self.assertEqual(res["partido"], "MDB")

    def test_resolve_by_id(self):
        res = resolve(parliamentarian_id=160541, house="camara")
        self.assertEqual(res["nome"], "Arthur Lira")
        self.assertEqual(res["nome_civil"], "ARTHUR CÉSAR PEREIRA DE LIRA")

    def test_not_found_and_invalid_house(self):
        self.assertIn("not found", resolve("Fulano", house="camara")["error"])
        self.assertIn("Invalid house", resolve("Lira", house="stf")["error"])


if __name__ == "__main__":
    unittest.main()
