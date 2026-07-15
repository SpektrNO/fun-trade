# Fund composition profiles (look-through sector / region / asset class)

Static breakdown snapshots per symbol, used with **`portfolio.json`** holdings to roll up portfolio-level allocation.

## Automated fetch

**ETFs and mutual funds (Nordnet)** — scrapes region/sector/asset exposure from Nordnet fund pages (same HTML payload for UCITS ETFs and NO mutual funds). Add slugs to `nordnet_slugs.json`:

```bash
cp fund_profiles/nordnet_slugs.json.example fund_profiles/nordnet_slugs.json
make fetch-profiles CLASS='etf mutual_fund'
```

**ETFs (EOD Historical Data, optional fallback)** — used in `auto` mode only when no Nordnet slug is mapped; requires `EOD_API_TOKEN` in `.env`:

```bash
make fetch-profiles CLASS=etf
# or: uv run funtrade-fetch-profiles --symbol VWCE.DE --source eod
```

**Mutual funds only (Nordnet):**

```bash
make fetch-profiles CLASS=mutual_fund

# one-off by URL:
uv run funtrade-fetch-profiles --symbol KLP.EM --nordnet-url \
  "https://www.nordnet.no/fond/liste/klp-aksje-fremvoksende-markeder-indeks-nok-8e00e38f"
```

Profiles are written to `fund_profiles/{symbol}.json` for the Portfolio tab.

## Manual template

One file per symbol, e.g. `DNB.GLOBAL.A.json`:

```json
{
  "symbol": "DNB.GLOBAL.A",
  "as_of": "2026-03-31",
  "regions": { "North America": 0.72, "Europe": 0.15, "Asia Pacific": 0.10, "Other": 0.03 },
  "sectors": { "Technology": 0.28, "Financials": 0.16, "Healthcare": 0.12 },
  "asset_classes": { "Equity": 0.97, "Cash": 0.03 }
}
```

Sources: Nordnet fund pages (ETFs and NO mutual funds), EOD fundamentals (ETF fallback), issuer factsheets, justETF (UCITS), manual entry.
