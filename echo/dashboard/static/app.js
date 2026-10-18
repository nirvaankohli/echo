'use strict';
const $ = (s, root=document) => root.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const marks = {gmail:'M','google-calendar':'31','google-drive':'△',notion:'N',linear:'◒',github:'GH',atlassian:'A',asana:'∴',figma:'F',canva:'C',webflow:'W',stripe:'S',hubspot:'H',sentry:'S',cloudflare:'☁',custom:'+'};
let state, view='connections', selected='gmail', query='', category='All categories', tab='all', busy=false, noticeTimer;
const main = $('#main');
function icon(id, large=false) { return `<span aria-hidden="true" class="provider-icon provider-${esc(id)} ${large?'large':''}">${esc(marks[id]||'+')}</span>`; }
function status(row) { return row.status==='connected' ? (row.enabled?'Connected':'Paused') : row.status==='error'?'Needs attention':'Setup needed'; }
function badge(row) { return `<span class="badge ${row.status==='connected'&&row.enabled?'good':row.status==='error'?'warn':''}">${esc(status(row))}</span>`; }
function date(value) { return value ? new Date(typeof value==='number'?value*1000:value).toLocaleString([], {dateStyle:'medium',timeStyle:'short'}) : 'Not checked yet'; }
function toast(message) { clearTimeout(noticeTimer); $('#notice').textContent=message; $('#notice').hidden=false; noticeTimer=setTimeout(()=>$('#notice').hidden=true,5500); }
async function api(path, data) {
  const response=await fetch('/app/'+path, {credentials:'same-origin', headers:data===undefined?{}:{'Content-Type':'application/json','X-Echo-Dashboard':'1'}, method:data===undefined?'GET':'POST', body:data===undefined?undefined:JSON.stringify(data)});
  const result=await response.json();
  if (!response.ok) { const error=new Error(result.error||'Could not complete that request.'); error.status=response.status; throw error; }
  return result;
}
function locked(message) {
  main.innerHTML=`<section class="locked"><div class="eyebrow">YOUR PRIVATE WORKSPACE</div><h1>Open Echo on your Mac.</h1><p>${esc(message)}</p><p>In the Echo project, open <strong>scripts → Echo Dashboard.command</strong> from your main Mac login. It signs this browser in securely.</p><button class="button" data-action="refresh">Try again</button></section>`;
  $('#agent-status').textContent='Sign-in needed';
}
async function refresh(render=true) {
  try {
    state=await api('api/state');
    $('#agent-status').innerHTML='<i></i>Agent running';
    $('#nav-count').textContent=state.connections.filter(c=>c.enabled&&c.status==='connected').length;
    if(render) draw();
  } catch(error) { if(error.status===401) locked(error.message); else toast(error.message); throw error; }
}
function navigate(next) { view=next; history.replaceState(null,'','#'+view); draw(); main.focus({preventScroll:true}); }
function heading(title, subtitle, action='') { return `<div class="page-heading"><div><div class="eyebrow">YOUR ECHO WORKSPACE</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div>${action}</div>`; }
function draw() {
  if (!state) return;
  document.querySelectorAll('[data-view]').forEach(el=>{el.classList.toggle('selected',el.dataset.view===view);el.setAttribute('aria-current',el.dataset.view===view?'page':'false');});
  const title={connections:'Connections',schedules:'Schedules',activity:'Activity',settings:'Settings'}[view]||'Connections';
  $('#breadcrumb').innerHTML=`Workspace <span>/</span> ${title}`;document.title='Echo · '+title;
  if(view==='connections') connections(); else if(view==='settings') settings(); else if(view==='schedules') schedules(); else activity();
}
function connections() {
  const connected=state.connections.filter(c=>c.enabled&&c.status==='connected');
  const chips=[...connected.map(c=>`<button class="strip-connection" data-provider="${esc(c.provider)}"><span class="tiny-dot"></span>${esc(c.label)}</button>`),
    ...state.existing.map(c=>`<span class="strip-connection">${esc(c.label)} <span class="badge">${c.enabled?'Configured':'Paused'}</span></span>`)];
  main.innerHTML=heading('Connections','Choose the tools Echo can use for you.', '<button class="button" data-provider="custom"><span aria-hidden="true">＋</span> Custom connection</button>')+
    `<section class="connected-strip" aria-label="Current connections"><span class="strip-title">In your workspace</span>${chips.join('')||'<span class="strip-empty">Your first connection starts below.</span>'}</section>
    <div class="workspace-grid"><section class="catalog" aria-label="Provider catalog"><div class="catalog-toolbar"><div class="search"><span aria-hidden="true">⌕</span><label class="sr-only" for="search">Search services</label><input id="search" type="search" placeholder="Search your apps…" value="${esc(query)}" autocomplete="off"></div><label for="category" class="sr-only">Filter by category</label><select id="category" class="filter-select">${['All categories',...new Set(state.providers.map(p=>p.category))].map(c=>`<option ${c===category?'selected':''}>${esc(c)}</option>`).join('')}</select></div>
    <div class="tabs" role="group" aria-label="Connection filter"><button class="tab ${tab==='all'?'selected':''}" data-tab="all" aria-pressed="${tab==='all'}">All apps <span>${state.providers.length}</span></button><button class="tab ${tab==='connected'?'selected':''}" data-tab="connected" aria-pressed="${tab==='connected'}">Your connections <span>${state.connections.length}</span></button></div><ul id="providers" class="provider-list"></ul>
    <div class="custom-row"><span aria-hidden="true">＋</span><div><h3>Something else in mind?</h3><p>Bring any compatible remote MCP service.</p></div><button class="button sm" data-provider="custom">Add</button></div></section><aside id="detail" class="detail-panel" aria-label="Connection details"></aside></div>`;
  renderProviders(); renderDetail();
}
function renderProviders() {
  const providers=state.providers.filter(p=>(!query||`${p.name} ${p.description}`.toLowerCase().includes(query.toLowerCase()))&&(category==='All categories'||p.category===category)&&(tab==='all'||state.connections.some(c=>c.provider===p.id)));
  $('#providers').innerHTML=providers.map(p=>{
    const good=state.connections.find(c=>c.provider===p.id&&c.enabled&&c.status==='connected');
    return `<li class="provider-row"><button class="provider-select ${selected===p.id?'active':''}" data-provider="${esc(p.id)}" aria-pressed="${selected===p.id}">${icon(p.id)}<span class="provider-text"><strong>${esc(p.name)}</strong><p>${esc(p.description)}</p></span><span class="provider-tail">${good?'<span class="badge good">Connected</span>':`<span class="badge">${p.auth==='google'?'Google account':p.auth==='token'?'Access token':p.client_required?'App setup':'Browser sign-in'}</span>`}<span class="arrow" aria-hidden="true">›</span></span></button></li>`;
  }).join('') || `<li class="empty"><h2>${tab==='connected'?'No matching connections':'No matching services'}</h2><p>${tab==='connected'?'Connect an app from the catalog, or change your filters.':'Try another name, or add a custom connection.'}</p></li>`;
  if(tab==='connected') state.connections.filter(c=>c.provider==='custom').forEach(c=>{
    $('#providers').insertAdjacentHTML('beforeend',`<li class="provider-row"><button class="provider-select" data-provider="custom">${icon('custom')}<span class="provider-text"><strong>${esc(c.label)}</strong><p>Custom service</p></span>${badge(c)}</button></li>`);
  });
}
function renderDetail() {
  const p=state.providers.find(p=>p.id===selected)||{id:'custom',name:'Custom connection',description:'Connect a remote service using its MCP server address.',auth:'oauth'};
  const accounts=state.connections.filter(c=>c.provider===p.id);
  const google=p.auth==='google';
  const setup=google&&!state.google_configured;
  $('#detail').innerHTML=`${icon(p.id,true)}<h2>${esc(p.name)}</h2><p class="description">${esc(p.description)}</p><div class="detail-divider"></div>
    <div class="feature-label">${google?'WHAT ECHO CAN ACCESS':'HOW IT CONNECTS'}</div><ul class="permission-list">${google?'<li>Read the account you choose</li><li>No sending, deleting, or modifying</li>':'<li>Sign in directly with the provider</li><li>Review access before connecting</li><li>Tools become available to Echo</li>'}</ul>
    ${p.note?`<div class="inline-info">${esc(p.note)}</div>`:''}
    ${accounts.map(c=>`<section class="account-row"><div class="account-name">${esc(c.label)}</div>${badge(c)}<p class="mini-label">${c.tool_count} tools · ${c.checked?'Checked '+esc(date(c.checked)):'Not verified yet'}</p>${c.error?`<p class="error">${esc(c.error)}</p>`:''}<div class="button-group"><button class="button sm" data-connection="${esc(c.id)}" data-action="check">Check</button><button class="button sm" data-connection="${esc(c.id)}" data-action="${c.enabled?'pause':'resume'}">${c.enabled?'Pause':'Resume'}</button><button class="button sm" data-connection="${esc(c.id)}" data-action="reconnect">Reconnect</button><button class="button sm danger" data-connection="${esc(c.id)}" data-action="disconnect">Remove</button></div></section>`).join('')}
    ${setup?'<div class="inline-info"><strong>One-time Google setup</strong>Add your Google app credentials once, then use browser sign-in for Gmail, Calendar, and Drive.</div><button class="button primary full" data-view="settings">Set up Google <span aria-hidden="true">↗</span></button>':`
    <form id="connect-form"><div class="field"><label for="connection-name">${accounts.length?'Another account':'Connection name'} <span class="small">(optional)</span></label><input id="connection-name" name="label" placeholder="${esc(p.name)}" maxlength="80" autocomplete="off"></div>
    ${p.id==='custom'?'<div class="field"><label for="server-url">Server address</label><input id="server-url" name="url" type="url" placeholder="https://service.com/mcp" required><p class="help">Use the remote MCP URL from your provider.</p></div><div class="field"><label for="auth-method">Sign-in method</label><select id="auth-method" name="auth"><option value="oauth">Browser sign-in (OAuth)</option value="token">Access token</option><option value="none">Public, no sign-in</option></select></div>':''}
    <div id="token-field" ${p.auth!=='token'?'hidden':''}><div class="field"><label for="access-token">Access token</label><input id="access-token" name="token" type="password" autocomplete="off" ${p.auth==='token'?'required':''}><p class="help">Stored privately on this Mac. Never sent to the model.</p></div></div>
    ${!google&&p.auth!=='token'?`<details id="oauth-settings" ${p.client_required?'open':''}><summary>${p.client_required?'Required app credentials':'Advanced: use your own OAuth app'}</summary><div class="field"><label for="oauth-client">Client ID</label><input id="oauth-client" name="client_id" ${p.client_required?'required':''} autocomplete="off"></div><div class="field"><label for="oauth-secret">Client secret</label><input id="oauth-secret" name="client_secret" type="password" ${p.client_required?'required':''} autocomplete="off"></div><p class="help">Register this callback with the provider:</p><code>${esc(state.mcp_redirect)}</code></details>`:''}
    <div id="form-error" class="error" role="alert" hidden></div><button class="button primary full" type="submit">${accounts.length?'Connect another account':google?'Continue with Google':'Connect '+esc(p.name)} <span aria-hidden="true">↗</span></button></form>`}
    <div id="flow-status" role="status" aria-live="polite"></div><p class="detail-note">${google?'Google sign-in opens in a new tab.':'Your provider may require an eligible plan or administrator access.'} ${p.docs?`<a href="${esc(p.docs)}" target="_blank" rel="noopener noreferrer">Setup guide ↗</a>`:''}</p><div class="detail-divider"></div><p class="detail-note">Credentials stay in your main Mac login. Connecting an account does not switch off Echo’s action checks.</p>`;
}
function settings() {
  main.innerHTML=heading('Settings','A few things that keep your workspace yours.')+
    `<section class="setting-section"><div class="section-title"><h2>Google app setup</h2><span class="badge ${state.google_configured?'good':''}">${state.google_configured?'Configured':'One-time setup'}</span></div><p>Your own Google OAuth app lets this local dashboard connect Gmail, Calendar, and Drive. You only need to do this once.</p>
    <ol class="setup-steps"><li><strong>Create or select a Google Cloud project.</strong> Enable the Gmail, Google Calendar, and Google Drive APIs you want to use. <a href="https://console.cloud.google.com/apis/library" target="_blank" rel="noopener noreferrer">Open API library ↗</a></li><li><strong>Configure the consent screen.</strong> For a personal testing app, add your Google email as a test user. Test-mode access may need reconnecting after seven days.</li><li><strong>Create an OAuth client of type Web application.</strong> Add this exact authorized redirect URI:<div class="copy-row"><code>${esc(state.google_redirect)}</code><button class="button sm" data-action="copy-google">Copy</button></div><a href="https://console.cloud.google.com/auth/clients" target="_blank" rel="noopener noreferrer">Open Google Auth Platform ↗</a></li></ol>
    <form id="google-form"><div class="field"><label for="credential-file">Import downloaded credentials (optional)</label><input id="credential-file" type="file" accept="application/json,.json"><p class="help">The file is read in your browser. Only the client ID and secret are saved locally.</p></div><div class="field"><label for="google-client-id">Client ID</label><input id="google-client-id" name="client_id" placeholder="…apps.googleusercontent.com" required autocomplete="off"></div><div class="field"><label for="google-client-secret">Client secret</label><input id="google-client-secret" name="client_secret" type="password" required autocomplete="off"><p class="help">Existing secrets are never displayed here.</p></div><div class="error" id="google-error" role="alert" hidden></div><div class="button-group"><button class="button primary" type="submit">Save Google setup</button><button type="button" class="button" data-view="connections">Back to connections</button></div></form></section>
    <section class="setting-section"><h2>On this Mac, for you</h2><p>This dashboard is available only on your Mac. Browser sessions expire after eight hours; reopen the dashboard launcher to sign in again. Removing a connection deletes Echo’s local credentials. You can also revoke the app’s access from the provider’s account settings.</p><div class="button-group"><button class="button" data-action="logout">Sign out of dashboard</button></div></section>`;
}
function schedules() {
  main.innerHTML=heading('Schedules','The things Echo will remember to do.')+`<div class="inline-info">Create or change a schedule by texting Echo, for example: “Every weekday at 8 AM, summarize my calendar.”</div>`+
    (state.schedules.length?`<div class="data-list">${state.schedules.map(s=>`<article class="data-row"><div><h3>${esc(s.title)}</h3><p>${esc(s.reminder||s.prompt)}</p><p class="metadata">${s.enabled?'Next: '+esc(date(s.next_run)):'No upcoming run'} · ${esc(s.timezone)}${s.last_run?' · Last run: '+esc(s.last_run.status):''}</p></div><span class="badge ${s.enabled?'good':''}">${s.enabled?'Scheduled':'Finished or cancelled'}</span></article>`).join('')}</div>`:'<div class="empty"><h2>A little help, right on time.</h2><p>Text Echo to create your first reminder. Saved schedules will appear here.</p></div>');
}
function activity() {
  main.innerHTML=heading('Activity','Recent work from your personal agent.')+(state.tasks.length?`<div class="data-list">${[...state.tasks].reverse().map(t=>`<article class="data-row"><div><h3>${esc(t.text)}</h3>${t.result?`<p>${esc(t.result.slice(0,650))}</p>`:''}<p class="metadata">${esc(date(t.created))}</p></div><span class="badge ${t.status==='completed'?'good':t.status==='failed'?'warn':''}">${esc(t.status.replaceAll('_',' '))}</span></article>`).join('')}</div>`:'<div class="empty"><h2>Ready when you are.</h2><p>Send Echo a message. Its work will appear here.</p></div>');
}
function flowMessage(text, url='') { const target=$('#flow-status'); if(target) target.innerHTML=`<div class="flow-status"><strong>${esc(text)}</strong>${url?`<p>Finish signing in with the provider, then return here.</p><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open sign-in page ↗</a>`:''}</div>`; }
async function watchFlow(id, popup) {
  let opened=false;
  for(let step=0;step<155;step++) {
    const flow=await api('api/flows/'+encodeURIComponent(id));
    if(flow.status==='authorize'&&flow.url) {
      if(!opened&&popup&&!popup.closed) {popup.location.href=flow.url;popup.opener=null;opened=true;}
      flowMessage('Waiting for your sign-in', flow.url);
    } else if(flow.status==='connected') {
      if(popup&&!opened) popup.close();
      await refresh();toast('Connected. Echo can use this service on its next task.');return;
    } else if(flow.status==='error') {
      if(popup&&!opened) popup.close();
      await refresh();flowMessage(flow.error||'Connection failed. Try again.');return;
    } else flowMessage('Checking your connection…');
    await new Promise(resolve=>setTimeout(resolve,2000));
  }
  if(popup&&!opened) popup.close();
  flowMessage('Sign-in timed out. Try connecting again.');
}
document.addEventListener('input', event=>{ if(event.target.id==='search') {query=event.target.value;renderProviders();} });
document.addEventListener('change', async event=>{
  if(event.target.id==='category') {category=event.target.value;renderProviders();}
  if(event.target.id==='auth-method') {const token=event.target.value==='token';$('#token-field').hidden=!token;$('#access-token').required=token;$('#oauth-settings').hidden=event.target.value!=='oauth';}
  if(event.target.id==='credential-file') {
    try { const file=event.target.files[0];if(!file)return;if(file.size>64000)throw new Error('Choose the small OAuth credentials JSON file.');const data=JSON.parse(await file.text()).web;if(!data?.client_id||!data?.client_secret)throw new Error('Choose credentials for a Google Web application.');$('#google-client-id').value=data.client_id;$('#google-client-secret').value=data.client_secret;toast('Credentials loaded. Save to finish setup.'); }
    catch(error){toast(error.message);}
  }
});
document.addEventListener('click', async event=>{
  if(event.target.closest('.brand')) {event.preventDefault();navigate('connections');return;}
  const el=event.target.closest('button');if(!el)return;
  if(el.dataset.view) {navigate(el.dataset.view);return;}
  if(el.dataset.provider) {selected=el.dataset.provider;if(view!=='connections'){navigate('connections');}else{renderDetail();renderProviders();}if(innerWidth<650)$('#detail').scrollIntoView({behavior:'auto',block:'start'});return;}
  if(el.dataset.tab) {tab=el.dataset.tab;connections();return;}
  const action=el.dataset.action||(el.id==='refresh'?'refresh':null);if(!action)return;
  try {
    if(action==='refresh'){el.disabled=true;await refresh();toast('Workspace refreshed.');}
    else if(action==='copy-google'){await navigator.clipboard.writeText(state.google_redirect);toast('Redirect URI copied.');}
    else if(action==='logout'){await api('logout',{});locked('You are signed out. Your agent keeps running.');}
    else if(el.dataset.connection) {
      if(busy){toast('Finish the current connection attempt first.');return;}
      if(action==='disconnect'&&!el.dataset.confirm){el.dataset.confirm='yes';el.textContent='Remove from Echo?';return;}
      busy=true;el.disabled=true;
      const popup=action==='reconnect'?window.open('about:blank','_blank'):null;
      try {const result=await api(`api/connections/${encodeURIComponent(el.dataset.connection)}/${action}`,{});if(result.flow)await watchFlow(result.flow,popup);else{await refresh();toast(action==='disconnect'?'Connection removed from Echo.':'Connection paused for new tasks.');}}
      catch(error){if(popup)popup.close();throw error;}finally{busy=false;}
    }
  } catch(error){toast(error.message);} finally{el.disabled=false;}
});
document.addEventListener('submit', async event=>{
  if(!['connect-form','google-form'].includes(event.target.id))return;
  event.preventDefault();const form=event.target;const button=$('button[type="submit"]',form);
  const errorBox=$(form.id==='google-form'?'#google-error':'#form-error');errorBox.hidden=true;
  const data=Object.fromEntries(new FormData(form));
  if(form.id==='google-form') {
    button.disabled=true;try{await api('api/google-client',data);form.reset();await refresh();toast('Google setup saved. Choose a Google service to sign in.');}catch(error){errorBox.textContent=error.message;errorBox.hidden=false;}finally{button.disabled=false;}return;
  }
  if(busy){toast('Finish the current connection attempt first.');return;}
  busy=true;button.disabled=true;data.provider=selected;
  const provider=state.providers.find(p=>p.id===selected);const method=provider?.auth||data.auth;
  const popup=['google','oauth'].includes(method)?window.open('about:blank','_blank'):null;
  if(popup)popup.document.body.textContent='Preparing secure sign-in…';
  try{const result=await api('api/connections',data);form.reset();await watchFlow(result.flow,popup);}
  catch(error){if(popup)popup.close();if(errorBox.isConnected){errorBox.textContent=error.message;errorBox.hidden=false;}else toast(error.message);}
  finally{busy=false;if(button.isConnected)button.disabled=false;}
});
(async()=>{
  try {
    const token=new URLSearchParams(location.hash.slice(1)).get('token');
    if(token){history.replaceState(null,'',location.pathname);await api('session',{token});}
    else if(['connections','schedules','activity','settings'].includes(location.hash.slice(1)))view=location.hash.slice(1);
    await refresh();
  } catch(error){if(error.status===401)locked(error.message);else main.innerHTML=`<section class="locked"><h1>Couldn’t open Echo.</h1><p>${esc(error.message)}</p><button class="button" data-action="refresh">Try again</button></section>`;}
})();
