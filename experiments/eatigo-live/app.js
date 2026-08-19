const map = L.map('map').setView([1.3521, 103.8198], 11.4);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);

function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[c]));}
function bucket(p){if(p>=50)return'best50';if(p>=40)return'best40';if(p>=30)return'best30';if(p>=20)return'best20';return'best10';}
function tooltip(r){
  const rows=(r.slots||[]).map(s=>`<tr><td>${esc(s.time)}</td><td><strong>${esc(s.discount)}%</strong></td></tr>`).join('');
  return `<div class="slots-card">
    <div class="slots-head"><strong class="restaurant-name">${esc(r.name)}</strong><span class="best-chip">${esc(r.best_today)}%</span></div>
    <div class="slot-address">${esc(r.address||'')}</div>
    <div class="slots-label">Today · remaining times</div>
    <div class="slots-scroll"><table>${rows}</table></div>
    <div class="best-row">Best remaining <strong>${esc(r.best_today)}%</strong></div>
  </div>`;
}

const dataPromise = window.EATIGO_TODAY
  ? Promise.resolve(window.EATIGO_TODAY)
  : fetch('data/eatigo_today.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error(r.status);return r.json();});

dataPromise.then(data=>{
  const renderStarted=performance.now();
  const usable=(data.restaurants||[]).filter(r=>r.lat!=null&&r.lng!=null&&r.best_today!=null&&r.slots?.length);
  const markers=[];
  for(const r of usable){
    const p=Number(r.best_today);
    const icon=L.divIcon({className:'',html:`<div class="discount-pin ${bucket(p)}">${p}%</div>`,iconSize:[52,34],iconAnchor:[26,17]});
    const m=L.marker([r.lat,r.lng],{icon})
      .addTo(map)
      .bindTooltip(tooltip(r),{
        direction:'auto', sticky:false, offset:[14,0], opacity:.99, className:'eatigo-slot-tooltip'
      });
    m.on('mouseover',()=>{
      const pin=m.getElement()?.querySelector('.discount-pin');
      if(pin) pin.classList.add('hovered');
    });
    m.on('mouseout',()=>{
      const pin=m.getElement()?.querySelector('.discount-pin');
      if(pin) pin.classList.remove('hovered');
    });
    m.on('click',()=>window.open(r.eatigo_url,'_blank','noopener'));
    markers.push(m);
  }
  if(markers.length){map.fitBounds(L.featureGroup(markers).getBounds().pad(.15),{maxZoom:14});}
  requestAnimationFrame(()=>{
    const renderMs=Math.round(performance.now()-renderStarted);
    const backend=data.refresh_seconds!=null?`${data.refresh_seconds}s backend refresh`:'backend time n/a';
    document.getElementById('status').textContent=`${usable.length} mapped / ${data.restaurants_attempted||data.restaurants?.length||0} tested · ${backend} · ${renderMs}ms map render · checked ${new Date(data.fetched_at).toLocaleString('en-SG',{timeZone:'Asia/Singapore'})} SGT`;
  });
}).catch(err=>{
  document.getElementById('status').textContent=`Experiment data unavailable: ${err}`;
  console.error(err);
});
