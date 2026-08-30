let vehicles = [], opportunityVehicles = [], chart, opportunityMode = 'interesting';
let liveVehicles = [], closedVehicles = [], opportunitiesIncludeAvoid = false;
let liveLoading = false, closedLoading = false, opportunityLoading = false;
let activeMainTab = 'opportunities';
const PAGE_SIZE = 18;
const visibleCounts = {opportunities: PAGE_SIZE, results: PAGE_SIZE, live: PAGE_SIZE};

const $ = id => document.getElementById(id);
const money = n => new Intl.NumberFormat('de-DE', {style:'currency', currency:'AED', maximumFractionDigits:0}).format(n || 0);
const euro = n => new Intl.NumberFormat('de-DE', {style:'currency', currency:'EUR', maximumFractionDigits:0}).format(n || 0);
const time = d => d ? new Intl.DateTimeFormat('de-DE', {dateStyle:'medium', timeStyle:'short'}).format(new Date(d)) : '—';
const relative = d => { const seconds=Math.max(0,Math.floor((new Date(d)-Date.now())/1000)); return `${Math.floor(seconds/3600)}:${String(Math.floor(seconds%3600/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`; };
const safe = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const recClass = recommendation => ({'KAUFEN':'buy','PRÜFEN':'review','MEIDEN':'avoid'}[recommendation] || 'review');
const isClosedVehicle = v => !['active','ending'].includes(v.status);

function closedPriceMeta(v) {
  const trusted = Boolean(v.price_data_valid && Number(v.final_bid) > 0);
  const observed = Number(v.last_live_bid || v.current_bid || 0);
  if (trusted) return {label:'ENDPREIS', price:Number(v.final_bid), note:'Verlässlich am Auktionsende erfasst'};
  if (observed > 0) return {label:'LETZTER ERFASSTER PREIS', price:observed, note:'Nicht als finaler Endpreis bestätigt'};
  return {label:'PREIS NICHT ERFASST', price:null, note:'Auktion beendet • kein verlässlicher Preis gespeichert'};
}

async function fetchJson(url, timeoutMs=12000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {signal: controller.signal, cache: 'no-store'});
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

function switchMainTab(tab, shouldScroll=true) {
  if (!['opportunities','results','live'].includes(tab)) return;
  activeMainTab = tab;
  document.querySelectorAll('.workspace-tab').forEach(button => button.classList.toggle('active', button.dataset.tab === tab));
  document.querySelectorAll('.workspace-view').forEach(view => view.classList.toggle('active', view.id === tab));
  if (history.replaceState) history.replaceState(null, '', `#${tab}`);
  if (shouldScroll) document.querySelector('.workspace-tabs')?.scrollIntoView({behavior:'smooth', block:'start'});
}

function refreshTab(tab=activeMainTab) {
  if (tab === 'live') return loadLive();
  if (tab === 'results') return loadClosed();
  return loadOpportunities(opportunitiesIncludeAvoid);
}

function updateTabCounts() {
  if ($('opportunitiesCount')) $('opportunitiesCount').textContent = opportunityVehicles.length;
  if ($('resultsCount')) $('resultsCount').textContent = closedVehicles.length;
  if ($('liveCount')) $('liveCount').textContent = liveVehicles.length;
}

function loadMoreMarkup(tab, shown, total) {
  const target = tab === 'opportunities' ? 'opportunityMore' : tab === 'results' ? 'closedMore' : 'liveMore';
  if (!$(target)) return;
  if (shown >= total) {
    $(target).innerHTML = '';
    return;
  }
  const remaining = total - shown;
  $(target).innerHTML = `<button class="load-more" onclick="showMore('${tab}')">Mehr anzeigen <strong>+${Math.min(PAGE_SIZE, remaining)}</strong></button>`;
}

function showMore(tab) {
  visibleCounts[tab] += PAGE_SIZE;
  if (tab === 'opportunities') renderOpportunities();
  if (tab === 'results') renderClosed();
  if (tab === 'live') renderLive();
}

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

const card = (v, closed=false, showPurchase=false) => {
  const priceMeta = closed ? closedPriceMeta(v) : null;
  const priceText = closed ? (priceMeta.price === null ? '—' : money(priceMeta.price)) : money(v.current_bid);
  const priceLabel = closed ? priceMeta.label : 'AKTUELLES GEBOT';
  const priceNote = closed ? priceMeta.note : `Endet in ${relative(v.auction_end_time)}`;
  return `<article class="card ${closed?'closed':''}" onclick="openVehicle(${v.id})"><img src="${safe((v.images||[])[0]||'')}" alt="${safe(v.title)}"><div class="meta"><p>LOS #${safe(v.lot_id)} • ${closed?'BEENDET':'LIVE'}</p><h3>${safe(v.title)}</h3><div class="row"><span>${v.bid_count || 0} Gebote<br><small class="${closed?'final':'ending'}">${safe(priceNote)}</small></span><span class="right"><small>${safe(priceLabel)}</small><b class="price">${priceText}</b></span></div><small>${closed?'Beendet':'Letzte Aktualisierung'}: ${time(closed?(v.finished_at||v.auction_end_time):v.updated_at)}</small>${showPurchase?purchaseMini(v):''}${germanySummary(v)}${(v.condition_tags||[]).slice(0,4).map(t=>`<span class="tag">${safe(t)}</span>`).join('')}</div></article>`;
};

function updateStats() {
  const max = closedVehicles.reduce((a,v)=>Math.max(a,(v.price_data_valid && v.final_bid)||0),0);
  const buy = opportunityVehicles.filter(v=>v.purchase_recommendation==='KAUFEN').length;
  $('stats').innerHTML=[['BEENDETE AUKTIONEN',closedVehicles.length],['LIVE VERFOLGT',liveVehicles.length],['AKTUELLE KAUFCHANCEN',buy],['HÖCHSTER VERLÄSSLICHER ENDPREIS',money(max)]].map(x=>`<div class="stat"><p>${x[0]}</p><b>${x[1]}</b></div>`).join('');
  updateTabCounts();
}

function filteredOpportunities() {
  let rows = opportunityVehicles;
  if (opportunityMode === 'interesting') rows = rows.filter(v => v.purchase_recommendation !== 'MEIDEN');
  if (opportunityMode === 'buy') rows = rows.filter(v => v.purchase_recommendation === 'KAUFEN');
  if (opportunityMode === 'review') rows = rows.filter(v => v.purchase_recommendation === 'PRÜFEN');
  if (opportunityMode === 'avoid') rows = rows.filter(v => v.purchase_recommendation === 'MEIDEN');
  return rows;
}

function renderOpportunities() {
  const rows = filteredOpportunities();
  const shown = Math.min(visibleCounts.opportunities, rows.length);
  $('opportunityCards').innerHTML = rows.slice(0, shown).map(v => card(v, v.status === 'finished', true)).join('') || '<div class="loading">Für diesen Filter gibt es aktuell keine Fahrzeuge.</div>';
  loadMoreMarkup('opportunities', shown, rows.length);
}

function renderLive() {
  const shown = Math.min(visibleCounts.live, liveVehicles.length);
  $('liveCards').innerHTML = liveVehicles.slice(0, shown).map(v=>card(v)).join('') || '<div class="loading">Derzeit keine verfolgte Live-Auktion.</div>';
  loadMoreMarkup('live', shown, liveVehicles.length);
}

function renderClosed() {
  const shown = Math.min(visibleCounts.results, closedVehicles.length);
  $('closedCards').innerHTML = closedVehicles.slice(0, shown).map(v=>card(v,true)).join('') || '<div class="loading">Noch keine beendete Auktion erfasst.</div>';
  loadMoreMarkup('results', shown, closedVehicles.length);
}

async function loadLive() {
  if (liveLoading) return;
  liveLoading = true;
  try {
    liveVehicles = await fetchJson('/api/auctions/live', 10000);
    vehicles = [...closedVehicles, ...liveVehicles];
    renderLive();
    updateStats();
  } catch (error) {
    if (!$('liveCards').querySelector('.card')) $('liveCards').innerHTML='<div class="loading">Live-Daten konnten nicht geladen werden. Neuer Versuch läuft automatisch…</div>';
    console.error('live feed', error);
  } finally {
    liveLoading = false;
  }
}

async function loadClosed() {
  if (closedLoading) return;
  closedLoading = true;
  try {
    closedVehicles = await fetchJson('/api/auctions/closed', 12000);
    vehicles = [...closedVehicles, ...liveVehicles];
    renderClosed();
    updateStats();
  } catch (error) {
    if (!$('closedCards').querySelector('.card')) $('closedCards').innerHTML='<div class="loading">Beendete Auktionen konnten nicht geladen werden. Neuer Versuch läuft automatisch…</div>';
    console.error('closed feed', error);
  } finally {
    closedLoading = false;
  }
}

async function loadOpportunities(includeAvoid=false) {
  if (opportunityLoading) return;
  opportunityLoading = true;
  if (includeAvoid && !opportunitiesIncludeAvoid) $('opportunityCards').innerHTML='<div class="loading">Vollständige Kaufanalyse wird geladen…</div>';
  try {
    const url = includeAvoid ? '/api/opportunities?include_avoid=true' : '/api/opportunities';
    opportunityVehicles = await fetchJson(url, 25000);
    opportunitiesIncludeAvoid = includeAvoid;
    renderOpportunities();
    updateStats();
  } catch (error) {
    if (!$('opportunityCards').querySelector('.card')) $('opportunityCards').innerHTML='<div class="loading">Kaufanalyse dauert länger. Live- und Endpreisdaten laufen unabhängig weiter.</div>';
    console.error('opportunities', error);
  } finally {
    opportunityLoading = false;
  }
}

async function setOpportunityFilter(mode) {
  opportunityMode = mode;
  visibleCounts.opportunities = PAGE_SIZE;
  document.querySelectorAll('.filterbar button').forEach(button => button.classList.toggle('active', button.dataset.mode === mode));
  if ((mode === 'avoid' || mode === 'all') && !opportunitiesIncludeAvoid) {
    await loadOpportunities(true);
    return;
  }
  renderOpportunities();
}

function load() {
  loadLive();
  loadClosed();
  loadOpportunities(opportunitiesIncludeAvoid);
}

function germanDetail(v) {
  const g=v.germany||{};
  const done=isClosedVehicle(v);
  const closedMeta=done?closedPriceMeta(v):null;
  const comparedPrice=done?closedMeta.price:v.current_bid;
  const comparedLabel=done?(closedMeta.label==='ENDPREIS'?'Erfasster Endpreis':'Letzter erfasster Preis'):'Aktuelles Gebot';
  if (g.status !== 'ready') return `<section class="germany-box"><h2>🇩🇪 Deutscher Marktvergleich</h2><p class="explain">${g.status==='unavailable'?'Kein ausreichend verlässlicher AutoScout24-Vergleich gefunden.':'Marktvergleich wird erstellt und erscheint automatisch.'}</p>${g.search_url?`<a href="${safe(g.search_url)}" target="_blank" rel="noopener">AutoScout24-Suche öffnen ↗</a>`:''}</section>`;
  return `<section class="germany-box"><div class="comparison-head"><div><p>AUTOSCOUT24 DEUTSCHLAND</p><h2>Deutscher Marktvergleich</h2></div><a href="${safe(g.search_url)}" target="_blank" rel="noopener">${g.comparable_count} Treffer anzeigen ↗</a></div><div class="comparison-grid"><div>Median Angebotspreis<b>${euro(g.median_price_eur)}</b></div><div>Deutsche Preisspanne<b>${euro(g.min_price_eur)} – ${euro(g.max_price_eur)}</b></div><div>Wert in AED<b>${money(g.market_value_aed)}</b></div><div>${safe(comparedLabel)}<b>${comparedPrice?money(comparedPrice):'—'}</b></div><div>Brutto-Preisspanne<b class="${g.gross_spread_aed>=0?'positive':'negative'}">${g.gross_spread_aed>=0?'+':''}${money(g.gross_spread_aed)}</b></div><div>Erwarteter Nettovorteil<b class="${v.estimated_net_profit_aed>=0?'positive':'negative'}">${v.estimated_net_profit_aed>=0?'+':''}${money(v.estimated_net_profit_aed)}</b></div></div></section>`;
}

function purchaseDetail(v) {
  const rec=v.purchase_recommendation||'PRÜFEN';
  const findings=(v.damage_findings||[]).length ? v.damage_findings.join(' • ') : (v.damage_data_available?'Keine kritischen Schadensmuster erkannt':'Keine ausreichenden Zustandsdaten');
  const flags=(v.damage_red_flags||[]).length ? `<div class="warning"><b>Kritische Hinweise:</b> ${safe(v.damage_red_flags.join(' • '))}</div>` : '';
  return `<section class="purchase-box ${recClass(rec)}"><div class="purchase-head"><div><p>KAUFANALYSE</p><h2>${safe(rec)}</h2></div><span class="purchase-badge ${recClass(rec)}">${safe(rec)}</span></div><p class="purchase-reason">${safe(v.purchase_reason||'')}</p><div class="analysis-grid"><div>Schadenrisiko<b>${Number(v.damage_risk_score||0)}/100</b></div><div>Reparierbarkeit<b>${Number(v.repairability_score||0)}/100</b></div><div>Geschätzte Reparatur<b>${repairText(v)}</b></div><div>Maximal sinnvolles Gebot<b>${v.max_bid_aed>0?money(v.max_bid_aed):'Noch offen'}</b></div><div>Erwarteter Nettovorteil<b class="${v.estimated_net_profit_aed>=0?'positive':'negative'}">${v.market_price>0?money(v.estimated_net_profit_aed):'Noch offen'}</b></div><div>Erwartete Marge<b>${v.market_price>0?`${Number(v.estimated_margin_percent||0).toFixed(1)} %`:'Noch offen'}</b></div></div>${flags}<div class="findings"><b>Erkannte Hinweise</b><br>${safe(findings)}</div><small>Die Reparaturkosten sind eine konservative Schätzung und keine Werkstattzusage.</small></section>`;
}

async function openVehicle(id) {
  try {
    const [v,h]=await Promise.all([fetchJson(`/api/vehicles/${id}`,15000),fetchJson(`/api/vehicles/${id}/history`,15000)]);
    const done=isClosedVehicle(v);
    const closedMeta=done?closedPriceMeta(v):null;
    const displayPrice=done?closedMeta.price:v.current_bid;
    const displayLabel=done?closedMeta.label:'AKTUELLES GEBOT';
    const displayNote=done?`<small>${safe(closedMeta.note)}</small>`:'';
    $('detail').innerHTML=`<div class="hero"><img src="${safe((v.images||[])[0]||'')}"><div><p>LOS #${safe(v.lot_id)} • ${done?'BEENDET':'LIVE'}</p><h1>${safe(v.title)}</h1><div class="price big">${displayPrice?money(displayPrice):'—'}</div><p>${safe(displayLabel)} • ${v.bid_count} GEBOTE</p>${displayNote}</div></div>${purchaseDetail(v)}${germanDetail(v)}<div class="grid">${[['Marke',v.make||'—'],['Modell / Ausstattung',[v.model,v.trim].filter(Boolean).join(' ')||'—'],['Baujahr',v.year||'—'],['Kilometerstand',v.mileage?`${v.mileage.toLocaleString('de-DE')} km`:'—'],['Kraftstoff',v.fuel||'—'],['Getriebe',v.transmission||'—'],['Karosserie',v.body_type||'—'],['Farbe',v.color||'—'],['FIN',v.vin||'Nicht veröffentlicht'],['Schlüssel',v.keys_available||'—'],['Auktionsende',time(v.auction_end_time)],['Schadensrisiko',`${v.damage_risk_score}/100`]].map(x=>`<div>${x[0]}<b>${safe(x[1])}</b></div>`).join('')}</div><div class="notes"><b>Zustand und Hinweise</b><br>${safe(v.damage_description||v.condition||'Keine Zustandsbeschreibung veröffentlicht.')}${v.inspection_report_url?`<br><br><a href="${safe(v.inspection_report_url)}" target="_blank" rel="noopener">Offiziellen Prüfbericht öffnen ↗</a>`:''}</div><div class="gallery">${(v.images||[]).map(x=>`<img src="${safe(x)}" loading="lazy">`).join('')}</div><h2>Erfasster Gebotsverlauf</h2>`;
    $('modal').showModal(); if(chart) chart.destroy();
    chart=new Chart($('chart'),{type:'line',data:{labels:h.map(x=>new Date(x.timestamp).toLocaleString()),datasets:[{data:h.map(x=>x.current_bid),borderColor:'#e9ba64',backgroundColor:'#e9ba6420',fill:true,tension:.25}]},options:{plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8d9bab'}},y:{ticks:{color:'#8d9bab'}}}}});
  } catch (error) {
    console.error('vehicle detail', error);
  }
}

const initialTab = location.hash.replace('#','');
switchMainTab(['opportunities','results','live'].includes(initialTab) ? initialTab : 'opportunities', false);
window.addEventListener('hashchange', () => {
  const tab = location.hash.replace('#','');
  if (['opportunities','results','live'].includes(tab)) switchMainTab(tab, false);
});

loadLive();
loadClosed();
loadOpportunities(false);
setInterval(loadLive, 2000);
setInterval(loadClosed, 10000);
setInterval(() => loadOpportunities(opportunitiesIncludeAvoid), 30000);