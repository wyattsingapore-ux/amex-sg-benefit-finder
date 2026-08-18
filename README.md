# Singapore Dining Benefit Finder — AMEX + GHA

An independent static map/search app for Singapore dining and lifestyle benefits:

- **Love Dining (LD)** — combines the current AMEX Love Dining hotel and restaurant partner pages.
- **Lifestyle Credit (LC)** — uses the current AMEX Platinum Credit Card Fashion & Dining Credit participating-merchant PDF.
- **Both (LD + LC)** — strict outlet/location-level intersection.
- **GHA List** — Singapore Pan Pacific Hotels Group operated restaurants and bars participating in Pan Pacific DISCOVERY dining savings.
- **GHA + LC** — GHA dining outlets that also match an AMEX Lifestyle Credit outlet at the same location.

## Official data sources

- Love Dining hotels: https://www.americanexpress.com/sg/benefits/love-dining/love-dining-hotels.html
- Love Dining restaurants: https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html
- Fashion & Dining Credit PDF: https://www.americanexpress.com/content/dam/amex/en-sg/benefits/platinum-credit-card-fashion-dining-credit-participating-merchants.pdf
- Pan Pacific DISCOVERY participating restaurants: https://www.panpacific.com/en/dining/pphg-fb.html
- Pan Pacific DISCOVERY benefit details: https://www.panpacific.com/en/panpacific-discovery/benefits.html

The GitHub Actions job refreshes all official sources daily immediately before deployment.

## Pan Pacific DISCOVERY / GHA dining

The site labels this view **GHA List** for convenience. The underlying source is the official Pan Pacific Hotels Group operated restaurant list for Pan Pacific DISCOVERY, which is part of GHA DISCOVERY.

Current published dining savings by Pan Pacific DISCOVERY status:

- Silver — 10%
- Gold — 15%
- Platinum — 20%
- Titanium — 25%

The programme terms apply, including exclusions such as alcoholic beverages and restrictions on combining the dining saving with other discounts/promotions. `GHA + LC` identifies location-level candidates where the restaurant is also on the AMEX LC list; it does not claim that every payment scenario is guaranteed to trigger LC.

## OneMap geocoding

For automatic token renewal, provide these GitHub repository secrets:

```text
ONEMAP_API_EMAIL=your OneMap account email
ONEMAP_API_PASSWORD=your OneMap account password
```

`geocode.py` authenticates only at build time, caches coordinates in `data/geocodes.json`, and never writes credentials into the website.

## GitHub Pages deployment

1. In **Settings → Secrets and variables → Actions**, add `ONEMAP_API_EMAIL` and `ONEMAP_API_PASSWORD`.
2. In **Settings → Pages**, set Source to **GitHub Actions**.
3. Run **Refresh AMEX + GHA data and deploy Pages** once from Actions. It then refreshes daily at 10:17 AM Singapore time.

## Matching rules

Intersections are calculated at **outlet + location level**. A brand is not marked as overlapping merely because different branches participate in different programmes. Matching first requires the same postal code (or a strong normalized-address match), then sufficiently similar outlet names.

The GHA pipeline additionally verifies that every restaurant found in the official Singapore section survives the merge exactly once. Parser sanity checks and tests fail the build rather than silently publish a severely truncated list when source structures change.

## Main files

- `index.html`, `styles.css`, `app.js` — static UI/map.
- `scripts/refresh_data.py` / `scripts/refresh_data_fixed.py` — AMEX LD + LC extraction.
- `scripts/augment_gha.py` — official Singapore GHA/Pan Pacific dining extraction and GHA+LC intersection.
- `scripts/geocode.py` — OneMap authentication/geocoding and cache.
- `scripts/validate_data.py` — data invariants.
- `data/merchants.json` — generated application dataset during deployment.
- `.github/workflows/refresh-and-deploy.yml` — daily refresh + GitHub Pages deployment.
- `tests/` — parser and intersection regression tests.

## Disclaimer

Unofficial community tool; not affiliated with American Express, GHA DISCOVERY or Pan Pacific Hotels Group. Always verify current eligibility, participating outlets, exclusions and programme/card terms before spending.
