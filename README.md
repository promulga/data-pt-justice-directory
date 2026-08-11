# data-pt-justice-directory

Directory of Portuguese justice-system entities — courts, prosecution,
prisons, julgados de paz, forensic medicine, arbitration centres, registries —
scraped from each family's authoritative official source. Mainland + Azores +
Madeira.

## Datasets

| File | Family | Source | Rows |
|------|--------|--------|------|
| `data/directory.csv` | Tribunais + Ministério Público | Citius (citius.mj.pt) | 427 |
| `data/prisoes.csv` | Estabelecimentos prisionais + centros educativos | DGRSP | 55 |
| `data/julgados_paz.csv` | Julgados de paz (sedes, delegações, balcões) | Conselho dos Julgados de Paz | 48 |
| `data/inmlcf.csv` | INMLCF — delegações + gabinetes médico-legais | inmlcf.justica.gov.pt | 36 |
| `data/arbitragem.csv` | Centros de arbitragem autorizados | DGPJ | 39 |
| `data/registos.csv` | Conservatórias / registos e notariado | IRN | (harvesting) |

Every dataset ships as CSV + JSON. The family datasets share a 13-column
schema: `name, category, address, postal_code, locality, district_or_region,
phone, fax, email, website, latitude, longitude, source_url` (coordinates only
where the source publishes them — no external geocoding). Each family's
scraper lives in `scrapers/<family>.py` (stdlib only).

Known coverage facts: the julgados de paz network has never covered the
Azores (Madeira yes, via Funchal); DGRSP publishes no per-establishment
emails; arbitration centres cluster in the major cities by nature.

## Citius directory (`data/directory.csv`)

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

## Scope notes

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
