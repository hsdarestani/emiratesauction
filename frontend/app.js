let vehicles = [], opportunityVehicles = [], chart, opportunityMode = 'interesting';
const money = n => new Intl.NumberFormat('de-DE', {style:'currency', currency:'AED', maximumFractionDigits:0}).format(n || 0);
const euro = n => new Intl.NumberFormat('de-DE', {style:'currency', currency:'EUR', maximumFractionDigits:0}).format(n || 0);
const time = d => d ? new Intl.DateTimeFormat('de-DE', {dateStyle:'medium', timeStyle:'short'}).format(new Date(d)) : '—';
const relative = d => { const seconds=Math.max(0,Math.floor((new Date(d)-Date.now())/1000)); return `${Math.floor(seconds/3600)}:${String(Math.floor(seconds%3600/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`; };
const safe = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const recClass = recommendation => ({'KAUFEN':'buy','PRÜFEN':'review','MEIDEN':'avoid'}[recommendation] || 'review');

function germanySummary(v) {
  const g = v.germany || {};
  const profit = Number.isFinite(v.estimated_net_profit_aed) ? v.estimated_net_profit_aed : g.estimated_net_profit_aed;
  if (g.status === 'ready') return `<div class="germany-line"><span>🇩🇪 Deutscher Median <b>${euro(g.median_price_eur)}</b><small>${g.comparable_count} ähnliche AutoScout24-Angebote</small></span><span class="profit ${profit >= 0 ? 'positive' : 'negative'}">${profit >= 0 ? '+' : ''}${money(profit)}<small>nach geschätzter Reparatur</small></span></div>`;
  if (g.status === 'unavailable') return `<div class="germany-line pending">🇩🇪 Noch kein verlässlicher deutscher Vergleich</div>`;
  return `<div class="germany-line pending">🇩🇪 Marktvergleich wird erstellt</div>`;
}

function repairText(v) {
  if (!v.repair_estimate_high_eur) return 'Noch nicht sicher bestimmbar';
  if (v.repair_estimate_low_eur === v.repair_estimate_high_eur) return euro(v.repair_estimate_low_eur);
  return `${euro(v.repair_estimate_low_eur)} – ${euro(v.repair_estimate_high_eur)}`;
}

function purchaseMini(v) {
  const rec = v.purchase_recommendation || 'PRÜFEN';
  return `<div class="purchase-mini ${recClass(rec)}"><div><span class="purchase-badge ${recClass(rec)}">${safe(rec)}</span><small>Schadenrisiko ${Number(v.damage_risk_score || 0)}/100</small></div><div class="purchase-numbers"><span>Reparatur <b>${repairText(v)}</b></span><span>${v.max_bid_aed > 0 ? `Max. Gebot <b>${money(v.max_bid_aed)}</b>` : 'Max. Gebot <b>noch offen</b>'}</span></div><p>${safe(v.purchase_reason || '')}</p></div>`;
}

const card = (v, closed=false, showPurchase=false) => `<article class="card ${closed?'closed':''}" onclick="openVehicle(${v.id})"><img src="${safe((v.images||[])[0]||'')}" alt="${safe(v.title)}"><div class="meta"><p>LOS #${safe(v.lot_id)} • ${closed?'BEENDET':'LIVE'}</p><h3>${safe(v.title)}</h3><div class="row"><span>${v.bid_count} Gebote<br><small class="${closed?'final':'ending'}">${closed?'Zuletzt live erfasster Preis bei Auktionsende':`Endet in ${relative(v.auction_end_time)}`}</small></span><span class="right"><small>${closed?'ENDPREIS':'AKTUELLES GEBOT'}</small><b class="price">${money(closed?v.final_bid:v.current_bid)}</b></span></div><small>Letzte Aktualisierung: ${time(v.updated_at)}</small>${showPurchase?purchaseMini(v):''}${germanySummary(v)}${(v.condition_tags||[]).slice(0,4).map(t=>`<span class="tag">${safe(t)}</span>`).join('')}</div></article>`;

function setOpportunityFilter(mode) {
  opportunityMode = mode;
  document.querySelectorAll('.filterbar button').forEach(button => button.classList.toggle('active', button.dataset.mode === mode));
  renderOpportunities();
}

function renderOpportunities() {
  let rows = opportunityVehicles;
  if (opportunityMode === 'interesting') rows = rows.filter(v => v.purchase_recommendation !== 'MEIDEN');
  if (opportunityMode === 'buy') rows = rows.filter(v => v.purchase_recommendation === 'KAUFEN');
  if (opportunityMode === 'review') rows = rows.filter(v => v.purchase_recommendation === 'PRÜFEN');
  if (opportunityMode === 'avoid') rows = rows.filter(v => v.purchase_recommendation === 'MEIDEN');
  opportunityCards.innerHTML = rows.map(v => card(v, v.status === 'finished', true)).join('') || '<div class="loading">Für diesen Filter gibt es aktuell keine Fahrzeuge.</div>';
}

async function load() {
  try {
    const [live, closed, opportunities] = await Promise.all([
      fetch('/api/auctions/live').then(r=>r.json()),
      fetch('/api/auctions/closed').then(r=>r.json()),
      fetch('/api/opportunities?include_avoid=true').then(r=>r.json())
    ]);
    vehicles = [...closed, ...live];
    opportunityVehicles = opportunities;
    const bids=closed.reduce((a,v)=>a+v.bid_count,0), max=closed.reduce((a,v)=>Math.max(a,v.final_bid||0),0), buy=opportunities.filter(v=>v.purchase_recommendation==='KAUFEN').length;
    stats.innerHTML=[['BEENDETE AUKTIONEN',closed.length],['LIVE VERFOLGT',live.length],['AKTUELLE KAUFCHANCEN',buy],['HÖCHSTER ERFASSTER ENDPREIS',money(max)]].map(x=>`<div class="stat"><p>${x[0]}</p><b>${x[1]}</b></div>`).join('');
    renderOpportunities();
    closedCards.innerHTML=closed.map(v=>card(v,true)).join('')||'<div class="loading">Noch keine verlässliche beendete Auktion erfasst.</div>';
    liveCards.innerHTML=live.map(v=>card(v)).join('')||'<div class="loading">Derzeit keine verfolgte Live-Auktion.</div>';
  } catch (error) {
    closedCards.innerHTML='<div class="loading">Auktionsdaten konnten nicht geladen werden.</div>';
    opportunityCards.innerHTML='<div class="loading">Kaufanalyse konnte nicht geladen werden.</div>';
  }
}

function germanDetail(v) {
  const g=v.germany||{};
  if (g.status !== 'ready') return `<section class="germany-box"><h2>🇩🇪 Deutscher Marktvergleich</h2><p class="explain">${g.status==='unavailable'?'Kein ausreichend verlässlicher AutoScout24-Vergleich gefunden.':'Marktvergleich wird erstellt und erscheint automatisch.'}</p>${g.search_url?`<a href="${safe(g.search_url)}" target="_blank" rel="noopener">AutoScout24-Suche öffnen ↗</a>`:''}</section>`;
  return `<section class="germany-box"><div class="comparison-head"><div><p>AUTOSCOUT24 DEUTSCHLAND</p><h2>Deutscher Marktvergleich</h2></div><a href="${safe(g.search_url)}" target="_blank" rel="noopener">${g.comparable_count} Treffer anzeigen ↗</a></div><div class="comparison-grid"><div>Median Angebotspreis<b>${euro(g.median_price_eur)}</b></div><div>Deutsche Preisspanne<b>${euro(g.min_price_eur)} – ${euro(g.max_price_eur)}</b></div><div>Wert in AED<b>${money(g.market_value_aed)}</b></div><div>${v.status==='finished'?'Erfasster Endpreis':'Aktuelles Gebot'}<b>${money(v.status==='finished'?v.final_bid:v.current_bid)}</b></div><div>Brutto-Preisspanne<b class="${g.gross_spread_aed>=0?'positive':'negative'}">${g.gross_spread_aed>=0?'+':''}${money(g.gross_spread_aed)}</b></div><div>Erwarteter Nettovorteil<b class="${v.estimated_net_profit_aed>=0?'positive':'negative'}">${v.estimated_net_profit_aed>=0?'+':''}${money(v.estimated_net_profit_aed)}</b></div></div></section>`;
}

function purchaseDetail(v) {
  const rec=v.purchase_recommendation||'PRÜFEN';
  const findings=(v.damage_findings||[]).length ? v.damage_findings.join(' • ') : (v.damage_data_available?'Keine kritischen Schadensmuster erkannt':'Keine ausreichenden Zustandsdaten');
  const flags=(v.damage_red_flags||[]).length ? `<div class="warning"><b>Kritische Hinweise:</b> ${safe(v.damage_red_flags.join(' • '))}</div>` : '';
  return `<section class="purchase-box ${recClass(rec)}"><div class="purchase-head"><div><p>KAUFANALYSE</p><h2>${safe(rec)}</h2></div><span class="purchase-badge ${recClass(rec)}">${safe(rec)}</span></div><p class="purchase-reason">${safe(v.purchase_reason||'')}</p><div class="analysis-grid"><div>Schadenrisiko<b>${Number(v.damage_risk_score||0)}/100</b></div><div>Reparierbarkeit<b>${Number(v.repairability_score||0)}/100</b></div><div>Geschätzte Reparatur<b>${repairText(v)}</b></div><div>Maximal sinnvolles Gebot<b>${v.max_bid_aed>0?money(v.max_bid_aed):'Noch offen'}</b></div><div>Erwarteter Nettovorteil<b class="${v.estimated_net_profit_aed>=0?'positive':'negative'}">${v.market_price>0?money(v.estimated_net_profit_aed):'Noch offen'}</b></div><div>Erwartete Marge<b>${v.market_price>0?`${Number(v.estimated_margin_percent||0).toFixed(1)} %`:'Noch offen'}</b></div></div>${flags}<div class="findings"><b>Erkannte Hinweise</b><br>${safe(findings)}</div><small>Die Reparaturkosten sind eine konservative Schätzung und keine Werkstattzusage.</small></section>`;
}

async function openVehicle(id) {
  const [v,h]=await Promise.all([fetch(`/api/vehicles/${id}`).then(r=>r.json()),fetch(`/api/vehicles/${id}/history`).then(r=>r.json())]);
  const done=v.status==='finished';
  detail.innerHTML=`<div class="hero"><img src="${safe((v.images||[])[0]||'')}"><div><p>LOS #${safe(v.lot_id)} • ${done?'BEENDET':'LIVE'}</p><h1>${safe(v.title)}</h1><div class="price big">${money(done?v.final_bid:v.current_bid)}</div><p>${done?'ENDPREIS':'AKTUELLES GEBOT'} • ${v.bid_count} GEBOTE</p>${done?'<small>Zuletzt live erfasster Preis bei Auktionsende</small>':''}</div></div>${purchaseDetail(v)}${germanDetail(v)}<div class="grid">${[['Marke',v.make||'—'],['Modell / Ausstattung',[v.model,v.trim].filter(Boolean).join(' ')||'—'],['Baujahr',v.year||'—'],['Kilometerstand',v.mileage?`${v.mileage.toLocaleString('de-DE')} km`:'—'],['Kraftstoff',v.fuel||'—'],['Getriebe',v.transmission||'—'],['Karosserie',v.body_type||'—'],['Farbe',v.color||'—'],['FIN',v.vin||'Nicht veröffentlicht'],['Schlüssel',v.keys_available||'—'],['Auktionsende',time(v.auction_end_time)],['Schadensrisiko',`${v.damage_risk_score}/100`]].map(x=>`<div>${x[0]}<b>${safe(x[1])}</b></div>`).join('')}</div><div class="notes"><b>Zustand und Hinweise</b><br>${safe(v.damage_description||v.condition||'Keine Zustandsbeschreibung veröffentlicht.')}${v.inspection_report_url?`<br><br><a href="${safe(v.inspection_report_url)}" target="_blank" rel="noopener">Offiziellen Prüfbericht öffnen ↗</a>`:''}</div><div class="gallery">${(v.images||[]).map(x=>`<img src="${safe(x)}" loading="lazy">`).join('')}</div><h2>Erfasster Gebotsverlauf</h2>`;
  modal.showModal(); if(chart) chart.destroy();
  chart=new Chart(document.getElementById('chart'),{type:'line',data:{labels:h.map(x=>new Date(x.timestamp).toLocaleString()),datasets:[{data:h.map(x=>x.current_bid),borderColor:'#e9ba64',backgroundColor:'#e9ba6420',fill:true,tension:.25}]},options:{plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8d9bab'}},y:{ticks:{color:'#8d9bab'}}}}});
}

load(); setInterval(load,2000);
