(() => {
    const rawFetch = window.fetch.bind(window);
    const chunkSize = 3 * 1024 * 1024;
    const sessionActions = new Set([
        '/api/import-url','/api/import-upload','/api/import-alerts','/api/agency-presets'
    ]);
    let expiresAt = null;
    let pendingSession;

    // Share the request limit with the same-origin schedule iframe.
    // Several panels ask for the same feed data after an import.
    const queue = [];
    const reads = new Map();
    let active = 0;
    function drain() {
        while (active === 0 && queue.length) {
            active++;
            const {input, init, resolve, reject} = queue.shift();
            rawFetch(input,init).then(resolve,reject).finally(() => { active--; drain(); });
        }
    }

    function enqueue(input, init, url) {
        const method = (init.method || 'GET').toUpperCase();
        const key = method === 'GET' ? url.href : null;
        let task = key && reads.get(key);
        if (!task) {
            task = new Promise((resolve,reject) => {
                const job = {input,init,resolve,reject};
                if (method === 'POST') queue.unshift(job);
                else queue.push(job);
                drain();
            });
            if (key) {
                reads.set(key,task);
                const clear = () => reads.delete(key);
                task.then(clear,clear);
            }
        }
        return key ? task.then(response => response.clone()) : task;
    }

    let requestGate = enqueue;
    try {
        if (window.parent !== window && typeof window.parent.gtfsHostedRequest === 'function') {
            requestGate = window.parent.gtfsHostedRequest;
        }
    } catch {
        // A standalone embed can use its own request queue.
    }
    window.gtfsHostedRequest = requestGate;

    function setSession(expires) {
        expiresAt = expires;
        window.dispatchEvent(new CustomEvent('gtfs-session-change',{detail:expires}));
    }

    async function ensureSession() {
        if (expiresAt && Date.parse(expiresAt) > Date.now() + 5000) return expiresAt;
        if (!pendingSession) {
            pendingSession = rawFetch('/api/session',{method:'POST'})
                .then(json)
                .then(data => {
                    setSession(data.expires_at);
                    return data.expires_at;
                })
                .finally(() => { pendingSession = null; });
        }
        return pendingSession;
    }

    async function json(response) {
        const value = await response.json();
        if (!response.ok) throw Error(value.error || 'The workbench request failed.');
        return value;
    }

    async function refreshAI() {
        const label = document.querySelector('#included-ai-status');
        if (!label) return;
        try {
            const data = await json(await rawFetch('/api/included-ai'));
            label.textContent = !data.enabled ? 'Included AI is currently unavailable.'
                : data.remaining === null ? 'Start a session to use included AI.'
                : data.remaining + ' of ' + data.session_cap + ' AI calls available for this session.';
            const button = document.querySelector('#assess-next');
            if (button) button.disabled = !data.enabled || data.remaining === 0;
        } catch { label.textContent = 'Could not check included AI availability.'; }
    }

    window.fetch = async (input, init = {}) => {
        const url = new URL(typeof input === 'string' ? input : input.url, location.href);
        const sameOrigin = url.origin === location.origin;
        if (sameOrigin && init.method === 'POST' && sessionActions.has(url.pathname)) {
            await ensureSession();
        }
        if (sameOrigin && url.pathname === '/api/import-upload' && init.body instanceof Blob) {
            const id = crypto.randomUUID();
            const parts = Math.ceil(init.body.size / chunkSize);
            for (let i = 0; i < parts; i++) {
                const response = await rawFetch('/api/upload/' + id + '/' + i, {
                    method:'POST', body:init.body.slice(i * chunkSize, (i + 1) * chunkSize)
                });
                if (!response.ok) await json(response);
            }
            return rawFetch('/api/upload/' + id + '/finish?parts=' + parts, {method:'POST', headers:init.headers});
        }
        const requestSession = expiresAt;
        const response = sameOrigin && url.pathname.startsWith('/api/')
            ? await requestGate(input,init,url)
            : await rawFetch(input,init);
        if (sameOrigin && response.status === 401 && requestSession && expiresAt === requestSession) {
            setSession(null);
        }
        if (sameOrigin && init.method === 'POST' && ([
            '/api/assess-next','/api/clarify-alert','/api/manual-edits'
        ].includes(url.pathname))) setTimeout(refreshAI,0);
        return response;
    };

    document.addEventListener('DOMContentLoaded', async () => {
        if (location.pathname === '/diagram.html') return;
        refreshAI();
        const bar = document.createElement('section');
        bar.className = 'card';
        const status = document.createElement('p');
        status.setAttribute('role','status');
        const start = document.createElement('button');
        const end = document.createElement('button');
        const connect = document.createElement('button');
        for (const [button, label] of [[start,'Start temporary session'],[connect,'Connect an MCP client'],[end,'End session']]) {
            button.type = 'button';
            button.className = 'btn';
            button.textContent = label;
        }
        const actions = document.createElement('div');
        actions.className = 'row';
        const settings = document.createElement('div');
        settings.hidden = true;
        actions.append(start,connect,end);
        bar.append(status,actions,settings);
        document.querySelector('.wrap')?.prepend(bar);

        function showSession(expires) {
            start.hidden = !!expires;
            end.hidden = !expires;
            connect.hidden = !expires;
            status.textContent = expires
                ? 'Temporary session expires at ' + new Date(expires).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}) + '.'
                : 'Import a feed to start a private temporary session.';
        }
        window.addEventListener('gtfs-session-change',event => {
            showSession(event.detail);
            refreshAI();
        });
        showSession(expiresAt);
        rawFetch('/api/session').then(json).then(data => {
            if (!expiresAt && !pendingSession) setSession(data.expires_at);
        }).catch(error => { if (!expiresAt) status.textContent = error.message; });
        start.onclick = async () => {
            start.disabled = true;
            try {
                await ensureSession();
                if (typeof showFeed === 'function') await showFeed();
            } catch (error) { status.textContent = error.message; }
            finally { start.disabled = false; }
        };
        end.onclick = async () => {
            if (!window.confirm('End this session and delete its feeds and drafts?')) return;
            end.disabled = true;
            try {
                const result = await rawFetch('/api/session/end',{method:'POST'});
                if (!result.ok) await json(result);
                location.replace('/');
            } catch (error) { status.textContent = error.message; end.disabled = false; }
        };
        connect.onclick = async () => {
            if (!settings.hidden) { settings.hidden = true; settings.replaceChildren(); return; }
            try {
                const data = await json(await rawFetch('/api/session/token',{method:'POST'}));
                const instructions = document.createElement('p');
                instructions.textContent = 'Add this URL to an MCP client that supports Streamable HTTP and custom Authorization headers. Keep the temporary token private.';
                const config = document.createElement('textarea');
                config.readOnly = true;
                config.setAttribute('aria-label','MCP client configuration');
                config.rows = 7;
                config.className = 'control';
                config.value = JSON.stringify({url:data.url,headers:{Authorization:'Bearer '+data.token}},null,2);
                settings.replaceChildren(instructions,config);
                settings.hidden = false;
            } catch (error) { status.textContent = error.message; }
        };

        document.addEventListener('click', async (event) => {
            const link = event.target.closest('a[href^="/download/"]');
            if (!link) return;
            event.preventDefault();
            if (link.dataset.busy) return;
            link.dataset.busy = '1';
            const original = link.textContent;
            try {
                const chunks = [];
                let count = 1, hash = '';
                for (let i=0; i<count; i++) {
                    link.textContent = 'Downloading part ' + (i+1) + ' of ' + count + '…';
                    const response = await rawFetch(link.pathname+'?part='+i+'&expected='+encodeURIComponent(hash));
                    if (!response.ok) await json(response);
                    const size = Number(response.headers.get('X-GTFS-ZIP-Size'));
                    hash = response.headers.get('X-GTFS-ZIP-SHA256');
                    if (!(size>0) || !hash) throw Error('The server did not return a complete ZIP.');
                    count = Math.ceil(size / chunkSize);
                    const buffer = await response.arrayBuffer();
                    if (buffer.byteLength !== Math.min(chunkSize,size-i*chunkSize)) throw Error('The ZIP download was incomplete.');
                    chunks.push(buffer);
                }
                const blob = new Blob(chunks,{type:'application/zip'});
                const target = URL.createObjectURL(blob);
                const download = document.createElement('a');
                download.href = target;
                download.download = link.download || 'gtfs-draft.zip';
                download.click();
                setTimeout(() => URL.revokeObjectURL(target),60000);
            } catch (error) {
                status.textContent = error.message;
                bar.scrollIntoView();
            } finally {
                link.textContent = original;
                delete link.dataset.busy;
            }
        });
    });
})();
