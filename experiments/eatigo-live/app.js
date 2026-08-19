const map = L.map('map').setView([1.3521, 103.8198], 11.4);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);

const markerLayer = L.layerGroup().addTo(map);
let originMarker = null;
let originCircle = null;
let origin = null;
let dataset = null;
let allMapped = [];
let lastRenderMs = 0;

const $ = id => document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function bucket(p){if(p>=50)return'best50';if(p>=40)return'best40';if(p>=30)return'best30';if(p>=20)return'best20';return'best10';}
function mins(t){const [h,m]=String(t||'').split(':').map(Number);return Number.isFinite(h)&&Number.isFinite(m)?h*60+m:null;}
function sgMinutesNow(){
  const parts=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Singapore',hour:'2-digit',minute:'2-digit',hour12:false}).formatToParts(new Date());
  const h=Number(parts.find(x=>x.type==='hour')?.value||0), m=Number(parts.find(x=>x.type==='minute')?.value||0);
  return h*60+m;
}
function haversineKm(a,b){
  const R=6371, rad=x=>x*Math.PI/180;
  const dLat=rad(b.lat-a.lat), dLon=rad(b.lng-a.lng);
  const q=Math.sin(dLat/2)**2+Math.cos(rad(a.lat))*Math.cos(rad(b.lat))*Math.sin(dLon/2)**2;
  return 2*R*Math.asin(Math.sqrt(q));
}
function filteredSlots(r){
  const mode=$('timeFilter').value;
  const now=sgMinutesNow();
  return (r.slots||[]).filter(s=>{
    const m=mins(s.time); if(m==null) return false;
    if(mode==='next2') return m>=now && m<=now+120;
    if(mode==='lunch') return m>=11*60 && m<=15*60;
    if(mode==='dinner') return m>=17*60 && m<=22*60;
    if(mode==='late') return m>=21*60;
    return true;
  });
}
function tooltip(r, slots, best, distance){
  const rows=slots.map(s=>`<tr><td>${esc(s.time)}</td><td><strong>${esc(s.discount)}%</strong></td></tr>`).join('');
  const cuisine=(r.cuisines||[]).join(' · ');
  const meta=[cuisine, distance!=null?`${distance.toFixed(distance<10?1:0)} km away`:null].filter(Boolean).join(' · ');
  return `<div class="slots-card">
    <div class="slots-head"><strong class="restaurant-name">${esc(r.name)}</strong><span class="best-chip">${esc(best)}%</span></div>
    <div class="slot-address">${esc(r.address||'')}</div>
    ${meta?`<div class="slot-meta">${esc(meta)}</div>`:''}
    <div class="slots-label">Today · matching times</div>
    <div class="slots-scroll"><table>${rows}</table></div>
    <div class="best-row">Best matching discount <strong>${esc(best)}%</strong></div>
  </div>`;
}
function setCompactMode(){map.getContainer().classList.toggle('map-compact',map.getZoom()<=11.5);}
map.on('zoomend',setCompactMode); setCompactMode();

function populateCuisine(data){
  const select=$('cuisineFilter');
  const types=(data.cuisine_types||[]).filter(x=>x?.name).slice().sort((a,b)=>a.name.localeCompare(b.name));
  for(const item of types){
    const o=document.createElement('option'); o.value=item.name; o.textContent=`${item.name} (${item.count})`; select.appendChild(o);
  }
}
function updateOriginVisual(){
  if(originMarker){map.removeLayer(originMarker);originMarker=null;}
  if(originCircle){map.removeLayer(originCircle);originCircle=null;}
  if(!origin) return;
  const icon=L.divIcon({className:'',html:'<div class="origin-pin"></div>',iconSize:[28,34],iconAnchor:[14,31]});
  originMarker=L.marker([origin.lat,origin.lng],{icon,zIndexOffset:3000}).addTo(map).bindTooltip(esc(origin.label||'Search origin'),{direction:'top'});
  const radius=Number($('radiusFilter').value||0);
  if(radius>0) originCircle=L.circle([origin.lat,origin.lng],{radius:radius*1000,weight:2,fillOpacity:.05}).addTo(map);
}
function setOrigin(lat,lng,label){
  origin={lat:Number(lat),lng:Number(lng),label};
  $('radiusFilter').disabled=false;
  $('locationState').textContent=`Distance origin: ${label}`;
  updateOriginVisual();
  render(true);
}
function clearOrigin(){
  origin=null; $('radiusFilter').value='0'; $('radiusFilter').disabled=true;
  $('locationState').textContent='Set a place or use your location to enable distance filtering.';
  updateOriginVisual();
}
function render(fit=false){
  if(!dataset) return;
  const started=performance.now();
  markerLayer.clearLayers();
  const cuisine=$('cuisineFilter').value;
  const minDiscount=Number($('discountFilter').value||0);
  const radius=Number($('radiusFilter').value||0);
  const shown=[];
  for(const r of allMapped){
    if(cuisine && !(r.cuisines||[]).some(c=>c===cuisine)) continue;
    const slots=filteredSlots(r); if(!slots.length) continue;
    const best=Math.max(...slots.map(s=>Number(s.discount)||0)); if(best<minDiscount) continue;
    const distance=origin?haversineKm(origin,{lat:Number(r.lat),lng:Number(r.lng)}):null;
    if(origin && radius>0 && distance>radius) continue;
    const icon=L.divIcon({className:'',html:`<div class="discount-pin ${bucket(best)}">${best}%</div>`,iconSize:[52,34],iconAnchor:[26,17]});
    const m=L.marker([r.lat,r.lng],{icon})
      .addTo(markerLayer)
      .bindTooltip(tooltip(r,slots,best,distance),{direction:'auto',sticky:false,offset:[14,0],opacity:.99,className:'eatigo-slot-tooltip'});
    m.on('mouseover',()=>m.getElement()?.querySelector('.discount-pin')?.classList.add('hovered'));
    m.on('mouseout',()=>m.getElement()?.querySelector('.discount-pin')?.classList.remove('hovered'));
    m.on('click',()=>window.open(r.eatigo_url,'_blank','noopener'));
    shown.push({marker:m,best,distance});
  }
  updateOriginVisual();
  if(fit && shown.length){
    const points=shown.map(x=>x.marker.getLatLng());
    if(origin) points.push(L.latLng(origin.lat,origin.lng));
    map.fitBounds(L.latLngBounds(points).pad(.12),{maxZoom:14});
  }
  lastRenderMs=Math.round(performance.now()-started);
  const bits=[`${shown.length} shown`,`${allMapped.length} mapped`];
  if(cuisine) bits.push(cuisine);
  if(minDiscount) bits.push(`${minDiscount}%+`);
  if(origin && radius) bits.push(`within ${radius} km`);
  bits.push(`${lastRenderMs}ms render`);
  $('status').textContent=bits.join(' · ');
}

async function setPlace(){
  const q=$('locationInput').value.trim(); if(!q) return;
  $('locationState').textContent=`Finding “${q}”…`;
  $('setLocation').disabled=true;
  try{
    const search=/singapore/i.test(q)?q:`${q}, Singapore`;
    const url=`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=sg&q=${encodeURIComponent(search)}`;
    const res=await fetch(url,{headers:{'Accept-Language':'en-SG,en;q=0.9'}});
    if(!res.ok) throw new Error(`location lookup ${res.status}`);
    const rows=await res.json(); if(!rows.length) throw new Error('No Singapore location found');
    setOrigin(Number(rows[0].lat),Number(rows[0].lon),rows[0].display_name||q);
  }catch(err){$('locationState').textContent=`Could not find that location: ${err.message||err}`;}
  finally{$('setLocation').disabled=false;}
}
function useMyLocation(){
  if(!navigator.geolocation){$('locationState').textContent='Browser location is not available here.';return;}
  $('locationState').textContent='Getting your location…'; $('useLocation').disabled=true;
  navigator.geolocation.getCurrentPosition(
    pos=>{ $('useLocation').disabled=false; setOrigin(pos.coords.latitude,pos.coords.longitude,'My current location'); },
    err=>{ $('useLocation').disabled=false; $('locationState').textContent=`Location unavailable: ${err.message}`; },
    {enableHighAccuracy:true,timeout:12000,maximumAge:60000}
  );
}

for(const id of ['cuisineFilter','discountFilter','timeFilter']) $(id).addEventListener('change',()=>render(true));
$('radiusFilter').addEventListener('change',()=>{updateOriginVisual();render(true);});
$('setLocation').addEventListener('click',setPlace);
$('useLocation').addEventListener('click',useMyLocation);
$('locationInput').addEventListener('keydown',e=>{if(e.key==='Enter')setPlace();});
$('resetFilters').addEventListener('click',()=>{
  $('cuisineFilter').value=''; $('discountFilter').value='0'; $('timeFilter').value='all'; $('locationInput').value=''; clearOrigin(); render(true);
});

const dataPromise = window.EATIGO_TODAY
  ? Promise.resolve(window.EATIGO_TODAY)
  : fetch('data/eatigo_today.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error(r.status);return r.json();});

dataPromise.then(data=>{
  dataset=data;
  allMapped=(data.restaurants||[]).filter(r=>r.lat!=null&&r.lng!=null&&r.slots?.length);
  populateCuisine(data);
  render(true);
  const backend=data.refresh_seconds!=null?`${data.refresh_seconds}s backend refresh`:'backend time n/a';
  const cuisineCoverage=data.restaurants_with_cuisine!=null?` · cuisine ${data.restaurants_with_cuisine}/${data.restaurants_attempted}`:'';
  $('status').textContent=`${allMapped.length} mapped / ${data.restaurants_attempted||data.restaurants?.length||0} restaurants · ${backend}${cuisineCoverage} · ${lastRenderMs}ms map render`;
}).catch(err=>{
  $('status').textContent=`Experiment data unavailable: ${err}`;
  console.error(err);
});
