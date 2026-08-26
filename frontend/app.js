let vehicles = [], chart;
const money = n => new Intl.NumberFormat('en-AE', {style:'currency', currency:'AED', maximumFractionDigits:0}).format(n || 0);
const euro = n => new Intl.NumberFormat('de-DE', {style:'currency', currency:'EUR', maximumFractionDigits:0}).format(n || 0);
const time = d => d ? new Intl.DateTimeFormat('en-AE', {dateStyle:'medium', timeStyle:'short'}).format(new Date(d)) : '—';
const safe = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

function germanySummary(v) {
  const g = v.germany || {};
  if (g.status === 'ready') return `<div class="germany-line"><span>🇩🇪 Germany median <b>${euro(g.median_price_eur)}</b><small>${g.comparable_count} similar AutoScout24 ads</small></span><span class="profit ${g.estimated_net_profit_aed >= 0 ? 'positive' : 'negative'}">${g.estimated_net_profit_aed >= 0 ? '+' : ''}${money(g.estimated_net_profit_aed)}<small>estimated margin</small></span></div>`;
  if (g.status === 'unavailable') return `<div class="germany-line pending">🇩🇪 No reliable German match yet</div>`;
  return `<div class="germany-line pending">🇩🇪 German comparison queued</div>`;
}

const card = (v, closed=false) => `<article class="card ${closed?'closed':''}" onclick="openVehicle(${v.id})"><img src="${safe((v.images||[])[0]||'')}" alt="${safe(v.title)}"><div class="meta"><p>LOT #${safe(v.lot_id)} • ${closed?'FINISHED':'LIVE'}</p><h3>${safe(v.title)}</h3><div class="row"><span>${v.bid_count} bids<br><small class="${closed?'final':'ending'}">${closed?'Finished':'Ends'} ${time(v.auction_end_time)}</small></span><span class="right"><small>${closed?'FINAL PRICE':'CURRENT BID'}</small><b class="price">${money(closed?v.final_bid:v.current_bid)}</b></span></div>${closed?germanySummary(v):''}${(v.condition_tags||[]).slice(0,4).map(t=>`<span class="tag">${safe(t)}</span>`).join('')}</div></article>`;

async function load() {
  try {
    const [live, closed] = await Promise.all([fetch('/api/auctions/live').then(r=>r.json()), fetch('/api/auctions/closed').then(r=>r.json())]);
    vehicles = [...closed, ...live];
    const bids=closed.reduce((a,v)=>a+v.bid_count,0), max=closed.reduce((a,v)=>Math.max(a,v.final_bid||0),0);
    stats.innerHTML=[['FINISHED RESULTS',closed.length],['LIVE TRACKED',live.length],['HISTORICAL BIDS',bids],['HIGHEST FINAL PRICE',money(max)]].map(x=>`<div class="stat"><p>${x[0]}</p><b>${x[1]}</b></div>`).join('');
    closedCards.innerHTML=closed.map(v=>card(v,true)).join('')||'<div class="loading">No finished auction has been captured yet.</div>';
    liveCards.innerHTML=live.map(v=>card(v)).join('')||'<div class="loading">No live tracked auctions.</div>';
  } catch (error) { closedCards.innerHTML='<div class="loading">Could not load auction data. Please refresh.</div>'; }
}

function germanDetail(v) {
  const g=v.germany||{};
  if (g.status !== 'ready') return `<section class="germany-box"><h2>🇩🇪 Germany market comparison</h2><p class="explain">${g.status==='unavailable'?'No sufficiently reliable AutoScout24 match was found for this model and year.':'Comparison is queued and will appear automatically.'}</p>${g.search_url?`<a href="${safe(g.search_url)}" target="_blank" rel="noopener">Open AutoScout24 search ↗</a>`:''}</section>`;
  return `<section class="germany-box"><div class="comparison-head"><div><p>AUTOSCOUT24 GERMANY</p><h2>German market comparison</h2></div><a href="${safe(g.search_url)}" target="_blank" rel="noopener">View ${g.comparable_count} matches ↗</a></div><div class="comparison-grid"><div>Median asking price<b>${euro(g.median_price_eur)}</b></div><div>German price range<b>${euro(g.min_price_eur)} – ${euro(g.max_price_eur)}</b></div><div>Value in AED<b>${money(g.market_value_aed)}</b></div><div>Final UAE bid<b>${money(v.final_bid)}</b></div><div>Gross price spread<b class="${g.gross_spread_aed>=0?'positive':'negative'}">${g.gross_spread_aed>=0?'+':''}${money(g.gross_spread_aed)}</b></div><div>Estimated margin<b class="${g.estimated_net_profit_aed>=0?'positive':'negative'}">${g.estimated_net_profit_aed>=0?'+':''}${money(g.estimated_net_profit_aed)}</b></div></div><p class="explain">Based on the median of ${g.comparable_count} comparable asking prices, model year ±1, at €1 = AED ${g.eur_aed_rate}. Estimated margin subtracts the final bid plus entered repair/import costs. It does not yet include auction fees, German customs/VAT, transport, registration or negotiation and is not a guaranteed resale profit.</p></section>`;
}

async function openVehicle(id) {
  const v=vehicles.find(x=>x.id===id), h=await fetch(`/api/vehicles/${id}/history`).then(r=>r.json());
  detail.innerHTML=`<div class="hero"><img src="${safe((v.images||[])[0]||'')}"><div><p>LOT #${safe(v.lot_id)} • ${safe(v.status.toUpperCase())}</p><h1>${safe(v.title)}</h1><div class="price big">${money(v.status==='closed'?v.final_bid:v.current_bid)}</div><p>${v.status==='closed'?'FINAL PRICE':'CURRENT BID'} • ${v.bid_count} BIDS</p></div></div>${v.status==='closed'?germanDetail(v):''}<div class="grid">${[['Make',v.make||'—'],['Model / Trim',[v.model,v.trim].filter(Boolean).join(' ')||'—'],['Year',v.year||'—'],['Mileage',v.mileage?`${v.mileage.toLocaleString()} km`:'—'],['Fuel',v.fuel||'—'],['Transmission',v.transmission||'—'],['Body type',v.body_type||'—'],['Color',v.color||'—'],['VIN',v.vin||'Not published'],['Keys',v.keys_available||'—'],['Finished',time(v.auction_end_time)],['Risk score',`${v.risk_score}/100`]].map(x=>`<div>${x[0]}<b>${safe(x[1])}</b></div>`).join('')}</div><div class="notes"><b>Condition & notes</b><br>${safe(v.damage_description||v.condition||'No condition description published.')}${v.inspection_report_url?`<br><br><a href="${safe(v.inspection_report_url)}" target="_blank" rel="noopener">Open official inspection report ↗</a>`:''}</div><div class="gallery">${(v.images||[]).map(x=>`<img src="${safe(x)}" loading="lazy">`).join('')}</div><h2>Recorded bid history</h2>`;
  modal.showModal(); if(chart) chart.destroy();
  chart=new Chart(document.getElementById('chart'),{type:'line',data:{labels:h.map(x=>new Date(x.timestamp).toLocaleString()),datasets:[{data:h.map(x=>x.current_bid),borderColor:'#e9ba64',backgroundColor:'#e9ba6420',fill:true,tension:.25}]},options:{plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8d9bab'}},y:{ticks:{color:'#8d9bab'}}}}});
}

load(); setInterval(load,60000);
