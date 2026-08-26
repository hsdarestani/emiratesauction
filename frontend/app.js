let vehicles = [], chart;
const money = n => new Intl.NumberFormat('de-DE', {style:'currency', currency:'AED', maximumFractionDigits:0}).format(n || 0);
const euro = n => new Intl.NumberFormat('de-DE', {style:'currency', currency:'EUR', maximumFractionDigits:0}).format(n || 0);
const time = d => d ? new Intl.DateTimeFormat('de-DE', {dateStyle:'medium', timeStyle:'short'}).format(new Date(d)) : '—';
const relative = d => { const seconds=Math.max(0,Math.floor((new Date(d)-Date.now())/1000)); return `${Math.floor(seconds/3600)}:${String(Math.floor(seconds%3600/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`; };
const safe = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

function germanySummary(v) {
  const g = v.germany || {};
  if (g.status === 'ready') return `<div class="germany-line"><span>🇩🇪 Deutscher Median <b>${euro(g.median_price_eur)}</b><small>${g.comparable_count} ähnliche AutoScout24-Angebote</small></span><span class="profit ${g.estimated_net_profit_aed >= 0 ? 'positive' : 'negative'}">${g.estimated_net_profit_aed >= 0 ? '+' : ''}${money(g.estimated_net_profit_aed)}<small>geschätzte Marge</small></span></div>`;
  if (g.status === 'unavailable') return `<div class="germany-line pending">🇩🇪 Noch kein verlässlicher deutscher Vergleich</div>`;
  return `<div class="germany-line pending">🇩🇪 Marktvergleich wird erstellt</div>`;
}

const card = (v, closed=false) => `<article class="card ${closed?'closed':''}" onclick="openVehicle(${v.id})"><img src="${safe((v.images||[])[0]||'')}" alt="${safe(v.title)}"><div class="meta"><p>LOS #${safe(v.lot_id)} • ${closed?'BEENDET':'LIVE'}</p><h3>${safe(v.title)}</h3><div class="row"><span>${v.bid_count} Gebote<br><small class="${closed?'final':'ending'}">${closed?'Zuletzt live erfasster Preis bei Auktionsende':`Endet in ${relative(v.auction_end_time)}`}</small></span><span class="right"><small>${closed?'ENDPREIS':'AKTUELLES GEBOT'}</small><b class="price">${money(closed?v.final_bid:v.current_bid)}</b></span></div><small>Letzte Aktualisierung: ${time(v.updated_at)}</small>${closed?germanySummary(v):''}${(v.condition_tags||[]).slice(0,4).map(t=>`<span class="tag">${safe(t)}</span>`).join('')}</div></article>`;

async function load() {
  try {
    const [live, closed] = await Promise.all([fetch('/api/auctions/live').then(r=>r.json()), fetch('/api/auctions/closed').then(r=>r.json())]);
    vehicles = [...closed, ...live];
    const bids=closed.reduce((a,v)=>a+v.bid_count,0), max=closed.reduce((a,v)=>Math.max(a,v.final_bid||0),0);
    stats.innerHTML=[['BEENDETE AUKTIONEN',closed.length],['LIVE VERFOLGT',live.length],['ERFASSTE GEBOTE',bids],['HÖCHSTER BESTÄTIGTER ENDPREIS',money(max)]].map(x=>`<div class="stat"><p>${x[0]}</p><b>${x[1]}</b></div>`).join('');
    closedCards.innerHTML=closed.map(v=>card(v,true)).join('')||'<div class="loading">Noch keine beendete Auktion erfasst.</div>';
    liveCards.innerHTML=live.map(v=>card(v)).join('')||'<div class="loading">Derzeit keine verfolgte Live-Auktion.</div>';
  } catch (error) { closedCards.innerHTML='<div class="loading">Auktionsdaten konnten nicht geladen werden.</div>'; }
}

function germanDetail(v) {
  const g=v.germany||{};
  if (g.status !== 'ready') return `<section class="germany-box"><h2>🇩🇪 Deutscher Marktvergleich</h2><p class="explain">${g.status==='unavailable'?'Kein ausreichend verlässlicher AutoScout24-Vergleich gefunden.':'Marktvergleich wird erstellt und erscheint automatisch.'}</p>${g.search_url?`<a href="${safe(g.search_url)}" target="_blank" rel="noopener">AutoScout24-Suche öffnen ↗</a>`:''}</section>`;
  return `<section class="germany-box"><div class="comparison-head"><div><p>AUTOSCOUT24 DEUTSCHLAND</p><h2>Deutscher Marktvergleich</h2></div><a href="${safe(g.search_url)}" target="_blank" rel="noopener">${g.comparable_count} Treffer anzeigen ↗</a></div><div class="comparison-grid"><div>Median Angebotspreis<b>${euro(g.median_price_eur)}</b></div><div>Deutsche Preisspanne<b>${euro(g.min_price_eur)} – ${euro(g.max_price_eur)}</b></div><div>Wert in AED<b>${money(g.market_value_aed)}</b></div><div>Bestätigter Endpreis<b>${money(v.final_bid)}</b></div><div>Brutto-Preisspanne<b class="${g.gross_spread_aed>=0?'positive':'negative'}">${g.gross_spread_aed>=0?'+':''}${money(g.gross_spread_aed)}</b></div><div>Geschätzte Marge<b class="${g.estimated_net_profit_aed>=0?'positive':'negative'}">${g.estimated_net_profit_aed>=0?'+':''}${money(g.estimated_net_profit_aed)}</b></div></div></section>`;
}

async function openVehicle(id) {
  const v=vehicles.find(x=>x.id===id), h=await fetch(`/api/vehicles/${id}/history`).then(r=>r.json());
  const done=v.status==='finished';
  detail.innerHTML=`<div class="hero"><img src="${safe((v.images||[])[0]||'')}"><div><p>LOS #${safe(v.lot_id)} • ${done?'BEENDET':'LIVE'}</p><h1>${safe(v.title)}</h1><div class="price big">${money(done?v.final_bid:v.current_bid)}</div><p>${done?'ENDPREIS':'AKTUELLES GEBOT'} • ${v.bid_count} GEBOTE</p>${done?'<small>Zuletzt live erfasster Preis bei Auktionsende</small>':''}</div></div>${done?germanDetail(v):''}<div class="grid">${[['Marke',v.make||'—'],['Modell / Ausstattung',[v.model,v.trim].filter(Boolean).join(' ')||'—'],['Baujahr',v.year||'—'],['Kilometerstand',v.mileage?`${v.mileage.toLocaleString('de-DE')} km`:'—'],['Kraftstoff',v.fuel||'—'],['Getriebe',v.transmission||'—'],['Karosserie',v.body_type||'—'],['Farbe',v.color||'—'],['FIN',v.vin||'Nicht veröffentlicht'],['Schlüssel',v.keys_available||'—'],['Auktionsende',time(v.auction_end_time)],['Risikowert',`${v.risk_score}/100`]].map(x=>`<div>${x[0]}<b>${safe(x[1])}</b></div>`).join('')}</div><div class="notes"><b>Zustand und Hinweise</b><br>${safe(v.damage_description||v.condition||'Keine Zustandsbeschreibung veröffentlicht.')}${v.inspection_report_url?`<br><br><a href="${safe(v.inspection_report_url)}" target="_blank" rel="noopener">Offiziellen Prüfbericht öffnen ↗</a>`:''}</div><div class="gallery">${(v.images||[]).map(x=>`<img src="${safe(x)}" loading="lazy">`).join('')}</div><h2>Erfasster Gebotsverlauf</h2>`;
  modal.showModal(); if(chart) chart.destroy();
  chart=new Chart(document.getElementById('chart'),{type:'line',data:{labels:h.map(x=>new Date(x.timestamp).toLocaleString()),datasets:[{data:h.map(x=>x.current_bid),borderColor:'#e9ba64',backgroundColor:'#e9ba6420',fill:true,tension:.25}]},options:{plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8d9bab'}},y:{ticks:{color:'#8d9bab'}}}}});
}

load(); setInterval(load,2000);
