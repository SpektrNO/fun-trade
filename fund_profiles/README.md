# Fund composition profiles (look-through sector / region / asset class)

Static breakdown snapshots per symbol, used with **`portfolio.json`** holdings to roll up portfolio-level allocation.

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

Sources: issuer factsheets, justETF (UCITS), manual entry for Norwegian mutual funds.

Not yet wired to the UI — loader and Portfolio tab coming next.
