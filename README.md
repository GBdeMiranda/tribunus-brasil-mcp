# Tribunus Brasil MCP

[![PyPI](https://img.shields.io/pypi/v/tribunus-brasil-mcp)](https://pypi.org/project/tribunus-brasil-mcp/)

> *Tribunus Plebis, auditing and scrutinizing public representatives in defense of the public interest and working people.*

**Tribunus Brasil MCP** is an open-source [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that connects Large Language Models directly to open data from the Brazilian National Congress (Federal Senate and Chamber of Deputies).

It structures a socio-economic analysis of each mandate (every merit vote in plenary, organized interests, destination of public money, publicity, agenda and discourse) from the standpoint of working people, and returns it with instructions for the LLM to interpret it: votes are read against the Constitution's social and labor rights with criteria from labor and political-economy research, and the mandate's own spending against the principles of art. 37 (*legalidade, impessoalidade, moralidade, publicidade, eficiência*). Criteria and sources: [methodology](docs/methodology.md).

**PyPI:** [`tribunus-brasil-mcp`](https://pypi.org/project/tribunus-brasil-mcp/) - **Changelog:** [releases](https://github.com/GBdeMiranda/tribunus-brasil-mcp/releases)

---

## Installation

The package is published on [PyPI](https://pypi.org/project/tribunus-brasil-mcp/). The server runs locally over stdio and reads only public data, with no API keys.

### MCP clients, with `uvx` (recommended)

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed, add the server to your MCP client configuration (e.g. `claude_desktop_config.json` or `mcp_config.json`). `uvx` downloads the package from PyPI and runs it in an isolated environment.

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

This `mcpServers` format is the one used by Claude Desktop, Cursor and Antigravity; clients with their own settings format, such as Zed, take the same command: `uvx tribunus-brasil-mcp`.

### Claude Code

```bash
claude mcp add --transport stdio tribunus-brasil -- uvx tribunus-brasil-mcp
```

More options in the [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

### pip

```bash
pip install tribunus-brasil-mcp
```

This installs the `tribunus-brasil-mcp` command, to be used as the `command` in the client configuration (with no `args`).

---

## Usage

Ask in natural language and the assistant chooses the tool. For example:

| Request | Tool |
| --- | --- |
| *Avalie o mandato do deputado `<nome>` desde 2019.* | `evaluateParliamentarian` |
| *O que dá para verificar sobre os gastos e as emendas da senadora `<nome>` em 2025?* | `auditPublicity` |
| *Como o Congresso tem tratado a inteligência artificial?* | `searchCongressionalThemes` |
| *Traga o texto do PL 2630/2020.* | `getBillText` |

The server also provides the prompt `avaliarParlamentar(nome, casa)`: a *parecer* on a mandate, written from `evaluateParliamentarian` by following its `instrucao`.

---

## Available Tools

Each tool queries the official open-data services (live APIs and yearly bulk files) and returns structured, LLM-optimized JSON. The two mandate-analysis tools also return an `instrucao`: the analytical framework the LLM follows to interpret the data.

### Mandate analysis

- **`evaluateParliamentarian(name, house, year, parliamentarianId, desde)`**
  - Investigative roadmap of a mandate from the standpoint of working people, organized by five axes (labor, income, social security and taxation; public services, social rights and environment; public patrimony, infrastructure and national sovereignty; public administration, justice and constitutional guarantees; transparency, morality and social control), returned with an `instrucao` for the LLM. Each bill is read by who gains and who pays, its effect on capital-labor relations, market dependence, the public fund and taxation, the fraction of capital it favors, hidden retrenchment, the evidence behind promised benefits, and self-dealing and clientelism.
  - `votacoes_por_eixo`: every merit vote in plenary, classified into the axes by the houses' official themes (transparency by what the bill regulates), with result, margin, party majority, government orientation, positioning under each government and absences in contested votes. `desde` extends the period (e.g. 2019) to compare behavior across governments.
  - `base_social`: parliamentary fronts grouped by organized-interest bloc, committees.
  - `fundo_publico`: individual amendments by year, function and recipient type, special transfers, territorial concentration and birthplace.
  - `publicidade`: the `auditPublicity` indicators.
  - `agenda_e_discurso`: bills and speeches since the start of the legislature.

- **`auditPublicity(name, house, year, parliamentarianId)`**
  - What a citizen can verify about a mandate: CEAP receipts published, supplier traceability against the Receita Federal registry, paid self-promotion (also in minimum wages), amendment destinations (including *transferências especiais*), plenary votes with a known position.
  - Each indicator carries counts and R$ values with their base and percentage, evidence, accountability questions and an `instrucao` for the LLM.
  - Keyless: bulk downloads (Portal da Transparência, Câmara, Senado) and public mirrors of the CNPJ registry (BrasilAPI, minhareceita.org) and the Banco Central minimum wage series.

### Parliamentarian records

- **`getParliamentarianProfile(name, house, includeVotes, limit, parliamentarianId)`**
  - Unified bicameral profile of any Brazilian parliamentarian (Senator or Federal Deputy): civil name, parliamentary name, house (`Senado Federal` or `Câmara dos Deputados`), party, state (UF), email, photo, active committee assignments, and authored legislative proposals.

- **`getParliamentarianVotes(name, house, limit, year, parliamentarianId)`**
  - Nominal roll-call voting records, detailing bill subjects and the legislator's specific vote positions (`Sim`, `Não`, `Abstenção`, `Obstrução`).
  - Reports plenary participation (roll calls held vs. votes cast); for the Senate, justified and unjustified absences are separated.

- **`getParliamentarianExpenses(name, house, year, month, limit, parliamentarianId)`**
  - Itemized and aggregated CEAP (*Cota para o Exercício da Atividade Parlamentar*) expenditures, built from the complete yearly records.
  - Total spending, categorical breakdown (airfare, housing, publicity, advisory), top suppliers, and (Chamber) receipts without a digitized document.

- **`getParliamentarianActivity(name, house, startDate, endDate, limit, parliamentarianId)`**
  - Institutional activity records, including plenary sessions, committee deliberations, public hearings, and floor speeches. Defaults to the current year.

### Bills and themes

- **`searchCongressionalThemes(theme, house, limit, year)`**
  - Synthesizes congressional activity surrounding a public policy topic (e.g. "inteligência artificial", "reforma tributária", "transição energética") into a consolidated bicameral overview of bills, speeches, hearings, committee agendas and roll-call votes.

- **`searchLegislativeProposals(keyword, year, limit)`**
  - Searches the Senate for specific legislative proposals and bills (e.g., `PL 2630/2020`) using keywords and optional year filters.

- **`getBillText(number, year)`**
  - Metadata, current status, primary document URL, and extracted text of a bill from the Senate (with automatic PDF text extraction).

### Identification and provenance

- `house` is `'auto'` (default, searches both houses), `'senado'` or `'camara'`.
- Parliamentarian tools accept `name` or `parliamentarianId` (Senate `CodigoParlamentar` or Chamber deputy id). A name shared by more than one parliamentarian (e.g. `"Lira"`: Arthur Lira, Átila Lira) returns the list of `candidatos`, to retry with the id.
- Every result carries a `metadados` block (source URL, period, record count, completeness, limitations), so each figure can be checked at its source.

---

## Data Sources

- **Federal Senate**: [legislative open data](https://legis.senado.leg.br/dadosabertos) and [administrative open data](https://adm.senado.gov.br/adm-dadosabertos/swagger-ui/index.html) (CEAPS)
- **Chamber of Deputies**: [open data API v2](https://dadosabertos.camara.leg.br/swagger/api.html) and yearly bulk files: CEAP (`https://www.camara.leg.br/cotas/Ano-{year}.csv.zip`) and roll-call votes (`https://dadosabertos.camara.leg.br/arquivos/`)
- **Budget amendments**: [Portal da Transparência bulk download](https://portaldatransparencia.gov.br/download-de-dados/emendas-parlamentares)
- **CNPJ registry**: [BrasilAPI](https://brasilapi.com.br/), with [Minha Receita](https://minhareceita.org/) as fallback
- **Minimum wage**: [Banco Central SGS series 1619](https://api.bcb.gov.br/dados/serie/bcdata.sgs.1619/dados?formato=json)

Bulk files are cached in `~/.cache/tribunus-brasil-mcp` (override with `TRIBUNUS_CACHE_DIR`): refreshed daily for the current year, monthly for past years. The first call for a year downloads ~70 MB for the Chamber; the first publicity audit also downloads the amendments file (~32 MB) and looks up each supplier CNPJ once (cached for 30 days).

## License

MIT License.

---

## Local Development / Installation from Source

1. **Clone the repository**:
   ```bash
   git clone https://github.com/GBdeMiranda/tribunus-brasil-mcp.git
   cd tribunus-brasil-mcp
   ```

2. **Install dependencies and editable package** (with [uv](https://docs.astral.sh/uv/); `pip install -e .` also works):
   ```bash
   uv sync
   ```

3. **Run the server manually**:
   ```bash
   uv run tribunus-brasil-mcp
   ```

4. **Run the tests**:
   ```bash
   uv run python -m unittest discover -s tests
   ```

To try the tools interactively, point the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) at the command `uv run tribunus-brasil-mcp`.

### Releasing

Bump `version` in `pyproject.toml` (`uv version --bump minor` or `--bump patch` also updates `uv.lock`) and merge into `main`. The [publish workflow](https://github.com/GBdeMiranda/tribunus-brasil-mcp/blob/main/.github/workflows/publish.yml) then publishes the version to [PyPI](https://pypi.org/project/tribunus-brasil-mcp/) and creates the `vX.Y.Z` tag and the [GitHub release](https://github.com/GBdeMiranda/tribunus-brasil-mcp/releases).
