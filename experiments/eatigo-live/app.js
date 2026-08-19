const map = L.map('map').setView([1.3521, 103.8198], 11.4);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);

function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function bucket(p){if(p>=50)return'best50';if(p>=40)return'best40';if(p>=30)return'best30';if(p>=20)return'best20';return'best10';}
function tooltip(r){
  const rows=(r.slots||[]).map(s=>`<tr><td>${esc(s.time)}</td><td><strong>${esc(s.discount)}%</strong></td></tr>`).join('');
  return `<div class="slots"><strong>${esc(r.name)}</strong><br>${esc(r.address||'')}<br><br><strong>Today</strong><table>${rows}</table><br>Best remaining: <strong>${esc(r.best_today)}%</strong></div>`;
}

fetch('data/eatigo_today.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error(r.status);return r.json();}).then(data=>{
  const usable=(data.restaurants||[]).filter(r=>r.lat!=null&&r.lng!=null&&r.best_today!=null&&r.slots?.length);
  document.getElementById('status').textContent=`${usable.length} mapped live-discount samples · checked ${new Date(data.fetched_at).toLocaleString('en-SG',{timeZone:'Asia/Singapore'})} SGT`;
  const markers=[];
  for(const r of usable){
    const p=Number(r.best_today);
    const icon=L.divIcon({className:'',html:`<div class="discount-pin ${bucket(p)}">${p}%</div>`,iconSize:[52,34],iconAnchor:[26,17]});
    const m=L.marker([r.lat,r.lng],{icon}).addTo(map).bindTooltip(tooltip(r),{direction:'top',sticky:true,opacity:.98});
    m.on('click',()=>window.open(r.eatigo_url,'_blank','noopener'));
    markers.push(m);
  }
  if(markers.length){map.fitBounds(L.featureGroup(markers).getBounds().pad(.15),{maxZoom:14});}
}).catch(err=>{
  document.getElementById('status').textContent=`Experiment data unavailable: ${err}`;
  console.error(err);
});
