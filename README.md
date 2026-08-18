# Singapore Dining Benefit Finder — AMEX + GHA + Eatigo

An independent static map/search app for Singapore dining and lifestyle benefits:

- **Love Dining (LD)** — combines the current AMEX Love Dining hotel and restaurant partner pages.
- **Lifestyle Credit (LC)** — uses the current AMEX Platinum Credit Card Fashion & Dining Credit participating-merchant PDF.
- **Both (LD + LC)** — strict outlet/location-level intersection.
- **GHA List** — Singapore Pan Pacific Hotels Group operated restaurants and bars participating in Pan Pacific DISCOVERY dining savings.
- **GHA + LC** — GHA dining outlets that also match an AMEX Lifestyle Credit outlet at the same location.
- **Eatigo List** — current Singapore restaurants discovered from Eatigo, including visible date/time discount slots.
- **Eatigo + LC** — Eatigo restaurants that also match an AMEX Lifestyle Credit outlet at the same location.

## Data sources

- Love Dining hotels: https://www.americanexpress.com/sg/benefits/love-dining/love-dining-hotels.html
- Love Dining restaurants: https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html
- Fashion & Dining Credit PDF: https://www.americanexpress.com/content/dam/amex/en-sg/benefits/platinum-credit-card-fashion-dining-credit-participating-merchants.pdf
- Pan Pacific DISCOVERY participating restaurants: https://www.panpacific.com/en/dining/pphg-fb.html
- Pan Pacific DISCOVERY benefit details: https://www.panpacific.com/en/panpacific-discovery/benefits.html
- Eatigo Singapore restaurant search: https://eatigo.com/en/regions/27/search

The GitHub Actions job refreshes all sources daily immediately before deployment.

## Eatigo

Eatigo is dynamic rather than a static merchant list. The build crawls the current Singapore result pages, stores branch IDs and current visible date/time discount slots, fetches/caches each branch address, filters out nearby non-Singapore results, and calculates `Eatigo + LC` at outlet + location level.

Branch address details are cached for 30 days to avoid repeatedly requesting every Eatigo restaurant page. Time-slot discounts are refreshed every daily build.

`Eatigo + LC` means the same restaurant/outlet currently appears in Eatigo and the AMEX LC merchant list. It is an eligibility intersection, **not a guarantee that every transaction will stack**. Eatigo's own restaurant conditions/terms can restrict combination with other promotions; verify the current restaurant conditions and AMEX LC eligibility before spending.

## Pan Pacific DISCOVERY / GHA dining

The site labels this view **GHA List** for convenience. The underlying source is the official Pan Pacific Hotels Group operated restaurant list for Pan Pacific DISCOVERY, which is part of GHA DISCOVERY.

Current published dining savings by Pan Pacific DISCOVERY status:

- Silver — 10%
- Gold — 15%
- Platinum — 20%
- Titanium — 25%

The programme terms apply, including exclusions and restrictions on combining the dining saving with other discounts/promotions. `GHA + LC` identifies location-level candidates where the restaurant is also on the AMEX LC list.

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
3. Run **Refresh AMEX + GHA + Eatigo data and deploy Pages** once from Actions. It then refreshes daily at 10:17 AM Singapore time.

## Matching rules

Intersections are calculated at **outlet + location level**. A brand is not marked as overlapping merely because different branches participate in different programmes. Matching first requires the same postal code (or a strong normalized-address match), then sufficiently similar outlet names.

The pipelines include source-count and merge-integrity checks so a page/PDF redesign or pagination failure causes the build to fail rather than silently publish a severely truncated list.

## Main files

- `index.html`, `styles.css`, `app.js` — static UI/map.
- `scripts/refresh_data.py` / `scripts/refresh_data_fixed.py` — AMEX LD + LC extraction.
- `scripts/augment_gha.py` — GHA/Pan Pacific dining extraction and GHA+LC intersection.
- `scripts/augment_eatigo.py` — Eatigo pagination crawl, time slots, branch cache and Eatigo+LC intersection.
- `scripts/geocode.py` — OneMap authentication/geocoding and cache.
- `scripts/validate_data.py` — data invariants.
- `.github/workflows/refresh-and-deploy.yml` — daily refresh + GitHub Pages deployment.
- `tests/` — parser and intersection regression tests.

## Disclaimer

Unofficial community tool; not affiliated with American Express, GHA DISCOVERY, Pan Pacific Hotels Group or Eatigo. Always verify current eligibility, discounts, participating outlets, exclusions and programme/card terms before spending.
