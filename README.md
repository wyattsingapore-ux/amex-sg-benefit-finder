# AMEX SG Benefit Finder — Love Dining + Lifestyle Credit

An independent static map/search app for Singapore American Express participating merchants:

- **Love Dining (LD)** — combines the current Love Dining hotel and restaurant partner pages.
- **Lifestyle Credit (LC)** — uses the current Platinum Credit Card Fashion & Dining Credit participating-merchant PDF (fashion + dining).
- **Both (LD + LC)** — a strict **outlet/location-level intersection**. A brand is not marked “Both” merely because different outlets occur in the two programs.

## Official data sources

- Love Dining hotels: https://www.americanexpress.com/sg/benefits/love-dining/love-dining-hotels.html
- Love Dining restaurants: https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html
- Fashion & Dining Credit PDF: https://www.americanexpress.com/content/dam/amex/en-sg/benefits/platinum-credit-card-fashion-dining-credit-participating-merchants.pdf

The included GitHub Actions job refreshes the official sources daily immediately before deploying the site.

## OneMap geocoding

For automatic token renewal, provide these GitHub repository secrets:

```text
ONEMAP_API_EMAIL=your OneMap account email
ONEMAP_API_PASSWORD=your OneMap account password
```

`geocode.py` authenticates only at build time, caches coordinates in `data/geocodes.json`, and never writes credentials into the website. Without OneMap credentials, the searchable merchant list still works; new locations simply do not receive map pins until geocoded.

## GitHub Pages deployment

1. In **Settings → Secrets and variables → Actions**, add `ONEMAP_API_EMAIL` and `ONEMAP_API_PASSWORD`.
2. In **Settings → Pages**, set Source to **GitHub Actions**.
3. Run **Refresh AMEX data and deploy Pages** once from Actions. It then refreshes daily at 10:17 AM Singapore time.

## Matching rule for “Both”

The build normalizes punctuation and accents, then compares LD/LC candidates only when they share the same postal code (or have a strong normalized-address match if a postal code is absent). It additionally requires a sufficiently similar outlet/brand name. This deliberately avoids false matches caused by a brand participating at different branches.

If AMEX materially changes its page/PDF structure, parser sanity checks and tests are intended to fail the build rather than silently publish a severely truncated list.

## Main files

- `index.html`, `styles.css`, `app.js` — static UI/map.
- `scripts/refresh_data.py` — LD HTML + LC PDF extraction and outlet-level merge.
- `scripts/geocode.py` — OneMap authentication/geocoding and cache.
- `scripts/validate_data.py` — data invariants.
- `data/merchants.json` — generated application dataset during deployment.
- `.github/workflows/refresh-and-deploy.yml` — daily refresh + GitHub Pages deployment.
- `tests/` — parser and intersection tests.

## Disclaimer

Unofficial community tool; not affiliated with American Express. Always verify current eligibility, participating outlets, exclusions, card eligibility and terms with American Express before spending.
