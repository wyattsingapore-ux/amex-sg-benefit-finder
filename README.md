# Singapore Dining Benefit Finder — AMEX + GHA + Eatigo

An independent static map/search app for Singapore dining and lifestyle benefits:

- **Love Dining (LD)** — combines the current AMEX Love Dining hotel and restaurant partner pages.
- **Lifestyle Credit (LC)** — uses the current AMEX Platinum Credit Card Fashion & Dining Credit participating-merchant PDF.
- **Both (LD + LC)** — strict outlet/location-level intersection.
- **GHA List** — Singapore Pan Pacific Hotels Group operated restaurants and bars participating in Pan Pacific DISCOVERY dining savings.
- **GHA + LC** — GHA dining outlets that also match an AMEX Lifestyle Credit outlet.
- **Eatigo List** — restaurants currently listed in Eatigo's Singapore search, with a direct Eatigo link.
- **Eatigo + LC** — Eatigo listings that can be matched safely to an AMEX Lifestyle Credit dining outlet.

## Data sources

- Love Dining hotels: https://www.americanexpress.com/sg/benefits/love-dining/love-dining-hotels.html
- Love Dining restaurants: https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html
- Fashion & Dining Credit PDF: https://www.americanexpress.com/content/dam/amex/en-sg/benefits/platinum-credit-card-fashion-dining-credit-participating-merchants.pdf
- Pan Pacific DISCOVERY participating restaurants: https://www.panpacific.com/en/dining/pphg-fb.html
- Pan Pacific DISCOVERY benefit details: https://www.panpacific.com/en/panpacific-discovery/benefits.html
- Eatigo Singapore restaurant search: https://eatigo.com/en/regions/27/search

The GitHub Actions job refreshes the source lists daily before deployment.

## Eatigo — deliberately simple

The Eatigo integration is intentionally **list-only**. It does **not** collect, store or synchronize Eatigo time slots or discount percentages.

The build only crawls Eatigo's paginated Singapore search result pages to capture:

- restaurant / outlet name;
- Eatigo branch ID and direct Eatigo URL;
- branch/property text already present in the listing name, when available.

For `Eatigo + LC`, the build compares the Eatigo restaurant name against AMEX LC dining entries. A branch/property qualifier such as `@ PARKROYAL COLLECTION Marina Bay` is used when available. If multiple LC branches have the same restaurant name and the branch cannot be resolved confidently, the matcher deliberately leaves it out rather than guessing.

Eatigo-only entries are list-first and are not given invented precise map coordinates. Open Eatigo from the restaurant card to check current availability, booking times and discounts.

## Pan Pacific DISCOVERY / GHA dining

The site labels this view **GHA List** for convenience. The underlying source is the official Pan Pacific Hotels Group operated restaurant list for Pan Pacific DISCOVERY, which is part of GHA DISCOVERY.

Current published dining savings by Pan Pacific DISCOVERY status:

- Silver — 10%
- Gold — 15%
- Platinum — 20%
- Titanium — 25%

The programme terms apply. `GHA + LC` identifies location-level candidates where the restaurant is also on the AMEX LC list.

## OneMap geocoding

For automatic token renewal, provide these GitHub repository secrets:

```text
ONEMAP_API_EMAIL=your OneMap account email
ONEMAP_API_PASSWORD=your OneMap account password
```

`geocode.py` authenticates only at build time, caches verified coordinates in `data/geocodes.json`, and skips approximate Eatigo-only location labels.

## GitHub Pages deployment

1. In **Settings → Secrets and variables → Actions**, add `ONEMAP_API_EMAIL` and `ONEMAP_API_PASSWORD`.
2. In **Settings → Pages**, set Source to **GitHub Actions**.
3. Run **Refresh AMEX + GHA + Eatigo data and deploy Pages** once from Actions. It then refreshes daily at 10:17 AM Singapore time.

## Matching rules

AMEX LD+LC and GHA+LC use outlet/location-level matching. Eatigo+LC uses conservative restaurant-name + branch/property matching because the simple Eatigo list intentionally does not open every restaurant detail page for full addresses.

The pipelines include source-count and merge-integrity checks so a source redesign or pagination failure does not silently publish a severely truncated list.

## Main files

- `index.html`, `styles.css`, `app.js` — static UI/map.
- `scripts/refresh_data.py` / `scripts/refresh_data_fixed.py` — AMEX LD + LC extraction.
- `scripts/augment_gha.py` — GHA/Pan Pacific dining extraction and GHA+LC intersection.
- `scripts/augment_eatigo.py` — lightweight Eatigo restaurant-list crawl and Eatigo+LC matching.
- `scripts/geocode.py` — OneMap authentication/geocoding and cache.
- `scripts/validate_data.py` — data invariants.
- `.github/workflows/refresh-and-deploy.yml` — daily refresh + GitHub Pages deployment.
- `tests/` — parser and intersection regression tests.

## Disclaimer

Unofficial community tool; not affiliated with American Express, GHA DISCOVERY, Pan Pacific Hotels Group or Eatigo. Always verify current eligibility, discounts, participating outlets, exclusions and programme/card terms before spending.
