(()=>{
 const originalFetch=window.fetch.bind(window);
 const eligible=new Set(['/api/assess-next','/api/clarify-alert','/api/analyze-alert','/api/manual-edits','/api/import-url','/api/import-upload']);
 window.fetch=(input,init={})=>{const raw=typeof input==='string'?input:input.url,url=new URL(raw,window.location.href),method=(init.method||'GET').toUpperCase();
  if(url.origin===window.location.origin&&method==='POST'&&eligible.has(url.pathname)){
   const service={url:document.getElementById('ai-endpoint')?.value.trim()||'',model:document.getElementById('ai-model')?.value.trim()||'',key:document.getElementById('ai-key')?.value||''};
   if(service.url&&service.model){const headers=new Headers(init.headers||{});headers.set('X-GTFS-AI-Service',btoa(unescape(encodeURIComponent(JSON.stringify(service)))));init={...init,headers}}
  }
  return originalFetch(input,init);
 };
 document.addEventListener('DOMContentLoaded',()=>document.getElementById('ai-clear').addEventListener('click',()=>{for(const id of ['ai-endpoint','ai-model','ai-key'])document.getElementById(id).value=''}));
})();
