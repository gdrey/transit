(()=>{
 const el=id=>document.getElementById(id);let busy=false;
 async function read(url){const r=await fetch(url);if(!r.ok)throw Error('Unavailable');return r.json()}
 async function refresh(){if(busy)return;busy=true;const [feed,edits,alerts,draft]=await Promise.allSettled(['/api/summary','/api/manual-edits','/api/alert-assessments','/api/draft-view'].map(read));
  el('tracker-feed').textContent=feed.status==='fulfilled'?'Imported':'Not imported';
  if(edits.status==='fulfilled'){const items=edits.value.items||[],active=items.filter(x=>x.active).length;el('tracker-edits').textContent=active?`${active} applied`:'No edits';const list=el('tracker-edit-items');list.replaceChildren();items.forEach(item=>{const row=document.createElement('div');row.className='tracker-edit';const text=document.createElement('div');text.textContent=item.id+' · '+item.text;const state=document.createElement('span');state.textContent=item.active?'Applied':'Removed';row.append(text,state);list.append(row)})}else el('tracker-edits').textContent='Import a feed';
  if(alerts.status==='fulfilled'&&alerts.value){const items=alerts.value.items||[];el('tracker-alerts').textContent=items.length?`${items.length} imported · ${items.filter(x=>['not_checked','queued','running'].includes(x.state)).length} pending`:'No alerts';const list=el('tracker-alert-items');list.replaceChildren();items.forEach(item=>{const row=document.createElement('div');row.className='tracker-edit';const text=document.createElement('div');text.textContent=(item.change_id||'Change')+' · '+((item.item||{}).text||'').replace(/\s+/g,' ').slice(0,100);const state=document.createElement('span');state.textContent=(item.state||'not checked').replace(/_/g,' ');row.append(text,state);list.append(row)})}else el('tracker-alerts').textContent='No alerts';
  el('tracker-review').textContent=draft.status==='fulfilled'&&draft.value.available?(draft.value.service_included?'Service draft available':'Feed edits draft available'):'No draft yet';busy=false
 }
 refresh();setInterval(refresh,5000);
})();
