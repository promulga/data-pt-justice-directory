# data-pt-justice-directory

Directory of Portuguese justice-system entities, scraped from the official
[Citius portal](https://www.citius.mj.pt/portal/ContactosTribunais.aspx)
(Ministério da Justiça).

## Data

`data/directory.csv` / `data/directory.json` — one row per entity:

| Field | Notes |
|-------|-------|
| localidade | town (189 covered — mainland, Azores, Madeira) |
| entity_id | Citius internal id |
| name | entity name (two-line names joined with " — ") |
| category | `tribunal` \| `ministerio_publico` |
| address, postal_code | postal address |
| phone, fax, email | contacts (100% have email) |
| latitude, longitude | GPS where the portal has it (sparse) |

Current snapshot: **427 entities** — 216 courts (tribunais + juízos) and
211 public-prosecution units (Ministério Público, DIAPs).

## Scope

The Citius directory covers **courts and Ministério Público only**. The other
justice-entity families are published elsewhere and are planned as companion
modules in this repo:

- Estabelecimentos prisionais — DGRSP
- Conservatórias / registos e notariado — IRN / justiça.gov.pt
- Julgados de paz — DGPJ / Conselho dos Julgados de Paz

## Usage

```bash
python3 scrape.py   # stdlib only, ~5 min: 189 postbacks + per-entity GPS lookups
```

The scraper bootstraps a fresh ASP.NET session (cookies + __VIEWSTATE), fires
one `ddlLocalidade` postback per town, parses the `pesquisaresultado` blocks
from the MicrosoftAjax delta, and enriches each entity via the
`GetMapInfo` JSON page method.
