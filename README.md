# Tribunus Brasil MCP

> *In the tradition of the Roman Tribunus Plebis, auditing and scrutinizing public representatives in defense of the public interest.*

**Tribunus Brasil MCP** is an open-source [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that connects Large Language Models directly to open data from the Brazilian National Congress (Federal Senate and Chamber of Deputies).

Beyond standard API data retrieval, it equips LLMs with structured transparency audits (CEAP expenditures, voting histories, institutional attendance) and qualitative evaluation rubrics (**Matriz de Postura Parlamentar - MPP**) across geopolitical, economic, democratic, and socio-environmental dimensions.

---

## Quickstart

### Using `uvx` (Recommended for Claude Desktop, Cursor, Zed, Antigravity)

Add the following to your MCP client configuration (e.g. `claude_desktop_config.json` or `mcp_config.json`):

```json
{
  "mcpServers": {
    "tribunus-brasil": {
      "command": "uvx",
      "args": ["tribunus-brasil-mcp"]
    }
  }
}
```

### Local Development / Installation from Source

1. **Clone the repository**:
   ```bash
   git clone https://github.com/GBdeMiranda/mcp_brl_congress.git
   cd mcp_brl_congress
   ```

2. **Install dependencies and editable package**:
   ```bash
   pip install -e .
   ```

3. **Run the server manually**:
   ```bash
   tribunus-brasil-mcp
   ```

---

## Available Tools

All tools return structured, LLM-optimized JSON.

Every parliamentarian tool accepts either `name` or `parliamentarianId` (Senate `CodigoParlamentar` or Chamber deputy id). Ambiguous names (e.g. `"Lira"` → Arthur Lira / Átila Lira) are never guessed: the tool returns an error with the list of `candidatos` to retry with. Results carry a `metadados` block (source URL, period, record count, completeness, limitations) so every figure can be audited.

1. **`getParliamentarianProfile(name, house, includeVotes, limit)`**:
   - Retrieves the unified bicameral profile of any Brazilian parliamentarian (Senator or Federal Deputy).
   - Consolidates civil name, parliamentary name, house (`Senado Federal` or `Câmara dos Deputados`), party, state (UF), email, photo, active committee assignments, and authored legislative proposals.
   - `house`: `'auto'` (default), `'senado'`, or `'camara'`.

2. **`searchLegislativeProposals(keyword, year, limit)`**:
   - Searches for specific legislative proposals and bills (e.g., `PL 2630/2020`) using keywords and optional year filters.

3. **`getBillText(number, year)`**:
   - Retrieves metadata, current status, primary document URL, and extracted textual content from bills (with automatic PDF text extraction).

4. **`getParliamentarianExpenses(name, house, year, month, limit)`**:
   - Retrieves itemized and aggregated CEAP (*Cota para o Exercício da Atividade Parlamentar*) expenditures.
   - Calculates total spending, categorical breakdown (airfare, housing, publicity, advisory), top suppliers, and (Chamber) receipts without a digitized document.
   - Built from the complete yearly records, not a single API page.

5. **`getParliamentarianActivity(name, house, startDate, endDate, limit)`**:
   - Retrieves institutional activity records including plenary sessions, committee deliberations, public hearings, and floor speeches.

6. **`getParliamentarianVotes(name, house, limit, year)`**:
   - Retrieves nominal roll-call voting records, detailing bill subjects and the legislator's specific vote positions (`Sim`, `Não`, `Abstenção`, `Obstrução`).
   - Reports plenary participation (roll calls held vs. votes cast); for the Senate, justified and unjustified absences are separated.

7. **`evaluateParliamentarian(name, house, year)`**:
   - Consolidates biographical data, CEAP expenses, committee assignments, authored proposals, attendance, and roll-call votes alongside the qualitative **Matriz de Postura Parlamentar (MPP)** guidelines.

8. **`searchCongressionalThemes(theme, house, limit, year)`**:
   - Synthesizes congressional activity surrounding a public policy topic (e.g. "inteligência artificial", "reforma tributária", "transição energética") into a consolidated bicameral overview.

---

## Data Sources

- **Federal Senate**: `https://legis.senado.leg.br/dadosabertos`
- **Chamber of Deputies**: `https://dadosabertos.camara.leg.br/api/v2`
- **Chamber bulk files** (CEAP, roll-call votes): `https://www.camara.leg.br/cotas/` and `https://dadosabertos.camara.leg.br/arquivos/`
- **Senate CEAPS**: `https://adm.senado.gov.br/adm-dadosabertos/api/v1`

Bulk files are cached in `~/.cache/tribunus-brasil-mcp` (override with `TRIBUNUS_CACHE_DIR`): refreshed daily for the current year, monthly for past years. The first call for a year downloads ~70 MB for the Chamber.

## License

MIT License.
