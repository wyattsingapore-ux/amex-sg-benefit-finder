const SINGAPORE = [1.3521, 103.8198];
let payload = { merchants: [], sources: {} };
let userPos = null;
let markers = [];

const map = L.map('map').setView(SINGAPORE, 11.25);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

const layer = L.layerGroup().addTo(map);
const $ = id => document.getElementById(id);

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function km(a, b) {
  const R = 6371;
  const r = x => x * Math.PI / 180;
  const d1 = r(b[0] - a[0]);
  const d2 = r(b[1] - a[1]);
  const q = Math.sin(d1 / 2) ** 2 +
    Math.cos(r(a[0])) * Math.cos(r(b[0])) * Math.sin(d2 / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(q));
}

function modeMatch(m, x) {
  if (x === 'ld') return !!m.ld;
  if (x === 'lc') return !!m.lc;
  if (x === 'both') return !!m.ld && !!m.lc;
  if (x === 'gha') return !!m.gha;
  if (x === 'ghalc') return !!m.gha && !!m.lc;
  if (x === 'eatigo') return !!m.eatigo;
  if (x === 'eatigolc') return !!m.eatigo && !!m.lc;
  return false;
}

function current() {
  const mode = $('benefitFilter').value;
  const cat = $('categoryFilter').value;
  const q = $('searchBox').value.trim().toLowerCase();
  const bounds = map.getBounds();

  let rows = payload.merchants.filter(m =>
    modeMatch(m, mode) && (cat === 'all' || m.category === cat)
  );

  if (q) {
    rows = rows.filter(m => [m.name, m.brand, m.gha_hotel, m.eatigo_location, m.address, m.postal_code]
      .filter(Boolean).join(' ').toLowerCase().includes(q));
  }

  if ($('viewportOnly').checked) {
    rows = rows.filter(m =>
      m.lat != null && m.lng != null && bounds.contains([m.lat, m.lng])
    );
  }

  rows = rows.map(m => ({
    ...m,
    _distance: userPos && m.lat != null && m.lng != null
      ? km(userPos, [m.lat, m.lng]) : null
  }));

  if ($('sortOrder').value === 'nearest' && userPos) {
    rows.sort((a, b) =>
      (a._distance ?? 1e9) - (b._distance ?? 1e9) || a.name.localeCompare(b.name)
    );
  } else {
    rows.sort((a, b) => a.name.localeCompare(b.name));
  }
  return rows;
}

function badges(m) {
  const out = [];
  if (m.ld && m.lc) out.push('<span class="badge both">LD + LC</span>');
  else {
    if (m.ld) out.push('<span class="badge ld">LOVE DINING</span>');
    if (m.lc && !m.gha && !m.eatigo) out.push('<span class="badge lc">LIFESTYLE CREDIT</span>');
  }
  if (m.gha && m.lc) out.push('<span class="badge ghalc">GHA + LC</span>');
  else if (m.gha) out.push('<span class="badge gha">GHA DINING</span>');
  if (m.eatigo && m.lc) out.push('<span class="badge eatigolc">EATIGO + LC</span>');
  else if (m.eatigo) out.push('<span class="badge eatigo">EATIGO</span>');
  out.push(`<span class="badge cat">${esc(m.category.toUpperCase())}</span>`);
  return out.join('');
}

function ghaTierNote(m) {
  if (!m.gha) return '';
  const t = m.gha_tiers || { silver: 10, gold: 15, platinum: 20, titanium: 25 };
  return `<div class="gha-note"><strong>DISCOVERY dining:</strong> Silver ${t.silver}% · Gold ${t.gold}% · Platinum ${t.platinum}% · Titanium ${t.titanium}%</div>`;
}

function eatigoNote(m) {
  if (!m.eatigo) return '';
  return '<div class="eatigo-note"><strong>Eatigo:</strong> Listed on Eatigo. Open Eatigo to check the current available times and discount.</div>';
}

function benefitText(m) {
  const b = [];
  if (m.ld) b.push('Love Dining');
  if (m.lc) b.push('Lifestyle Credit');
  if (m.gha) b.push('GHA / Pan Pacific DISCOVERY');
  if (m.eatigo) b.push('Eatigo');
  return b.join(' + ');
}

function updateNotice(rows) {
  const banner = $('bootstrapBanner');
  const mode = $('benefitFilter').value;
  if (payload.bootstrap_lc_only) {
    banner.classList.remove('hidden');
    banner.textContent = 'Bootstrap preview: Lifestyle Credit data is loaded, but Love Dining requires a live refresh.';
    return;
  }
  if (mode === 'eatigo' && rows.length && markers.length < rows.length) {
    banner.classList.remove('hidden');
    banner.innerHTML = `<strong>${markers.length} of ${rows.length} Eatigo restaurants are mapped.</strong> Any unmapped outlet is still shown in the list below.`;
    return;
  }
  if (rows.length && markers.length === 0) {
    banner.classList.remove('hidden');
    banner.innerHTML = `<strong>${rows.length} merchants matched, but map coordinates are not available yet.</strong> The merchant list is still available below the map.`;
    return;
  }
  if (rows.length && markers.length < rows.length) {
    banner.classList.remove('hidden');
    banner.innerHTML = `<strong>${markers.length} of ${rows.length} matching outlets are currently mapped.</strong> Unmapped outlets are still shown below.`;
    return;
  }
  banner.classList.add('hidden');
  banner.textContent = '';
}

function listHintForMode(mode) {
  if (mode === 'both') return 'Only outlets matched in both official AMEX sources at the same location.';
  if (mode === 'gha') return 'Singapore outlets on the official Pan Pacific DISCOVERY participating restaurant list.';
  if (mode === 'ghalc') return 'GHA dining outlets that also match an AMEX Lifestyle Credit outlet at the same location.';
  if (mode === 'eatigo') return 'Restaurants listed on Eatigo Singapore. Open Eatigo to check the current discount and booking time.';
  if (mode === 'eatigolc') return 'Eatigo restaurants that also match an AMEX Lifestyle Credit dining outlet at the same location. Open Eatigo to check the current offer.';
  return 'Results from the selected official merchant source.';
}

function render(fit = false) {
  const rows = current();
  layer.clearLayers();
  markers = [];

  for (const m of rows) {
    if (m.lat == null || m.lng == null) continue;
    const mk = L.circleMarker([m.lat, m.lng], { radius: 7, weight: 2, fillOpacity: .88 })
      .bindPopup(`<strong>${esc(m.name)}</strong><br>${esc(m.address)}<br><small>${esc(benefitText(m))}</small>`);
    mk.addTo(layer);
    markers.push(mk);
  }

  $('resultCount').textContent = rows.length;
  $('mappedCount').textContent = markers.length;
  $('listHint').textContent = listHintForMode($('benefitFilter').value);

  $('merchantList').innerHTML = rows.length
    ? rows.map(m => {
        const q = encodeURIComponent(`${m.name} ${m.address}`);
        const brand = m.brand && m.brand.toLowerCase() !== m.name.toLowerCase()
          ? `<div class="brand">${esc(m.brand)}</div>` : '';
        const eatigoLink = m.eatigo_url
          ? `<a target="_blank" rel="noopener" href="${esc(m.eatigo_url)}">View on Eatigo ↗</a>` : '';
        return `<article class="merchant">
          <div class="badges">${badges(m)}</div>
          <div><h3>${esc(m.name)}</h3>${brand}</div>
          <div class="address">${esc(m.address)}</div>
          ${ghaTierNote(m)}
          ${eatigoNote(m)}
          <div class="merchant-actions">
            <div class="action-links">${eatigoLink}<a target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=${q}">Maps ↗</a></div>
            <span class="distance">${m._distance != null ? m._distance.toFixed(1) + ' km' : ''}</span>
          </div>
        </article>`;
      }).join('')
    : '<div class="empty">No merchants match this filter. Try another benefit, category or search term.</div>';

  updateNotice(rows);
  if (fit && markers.length) {
    const fg = L.featureGroup(markers);
    map.fitBounds(fg.getBounds().pad(.15), { maxZoom: 15 });
  }
}

async function load() {
  try {
    const r = await fetch('data/merchants.json', { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    payload = await r.json();
    const dt = payload.generated_at ? new Date(payload.generated_at) : null;
    $('updated').textContent = dt
      ? `Data refreshed ${dt.toLocaleString('en-SG', { timeZone: 'Asia/Singapore' })} SGT`
      : 'Data has not been refreshed yet';
    $('ldHotelsSource').href = payload.sources.love_dining_hotels || '#';
    $('ldRestaurantsSource').href = payload.sources.love_dining_restaurants || '#';
    $('lcSource').href = payload.sources.lifestyle_credit_pdf || '#';
    $('ghaSource').href = payload.sources.gha_dining || 'https://www.panpacific.com/en/dining/pphg-fb.html';
    $('eatigoSource').href = payload.sources.eatigo || 'https://eatigo.com/en/regions/27/search';
    if (payload.bootstrap_lc_only) $('benefitFilter').value = 'lc';
    render(true);
  } catch (e) {
    $('merchantList').innerHTML = '<div class="empty">Could not load merchant data. Run the refresh script first.</div>';
    $('updated').textContent = 'Merchant data unavailable';
    console.error(e);
  }
}

$('benefitFilter').addEventListener('change', () => {
  const mode = $('benefitFilter').value;
  if (['gha', 'ghalc', 'eatigo', 'eatigolc'].includes(mode)) $('categoryFilter').value = 'dining';
  else if (mode !== 'lc') $('categoryFilter').value = 'all';
  render(true);
});
$('categoryFilter').addEventListener('change', () => render(true));
$('searchBox').addEventListener('input', () => render(false));
$('searchBox').addEventListener('keydown', e => { if (e.key === 'Enter') render(true); });
$('findBtn').addEventListener('click', () => render(true));
$('sortOrder').addEventListener('change', () => render(false));
$('viewportOnly').addEventListener('change', () => render(false));
map.on('moveend', () => { if ($('viewportOnly').checked) render(false); });

$('locateBtn').addEventListener('click', () => {
  if (!navigator.geolocation) return alert('Geolocation is not supported by this browser.');
  navigator.geolocation.getCurrentPosition(pos => {
    userPos = [pos.coords.latitude, pos.coords.longitude];
    L.circleMarker(userPos, { radius: 8, weight: 3, fillOpacity: 1 }).addTo(map).bindPopup('You are here');
    map.setView(userPos, 14);
    $('sortOrder').value = 'nearest';
    render(false);
  }, err => alert('Location permission was not available: ' + err.message), {
    enableHighAccuracy: true, timeout: 10000
  });
});

load();
