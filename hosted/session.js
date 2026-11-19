(() => {
    const rawFetch = window.fetch.bind(window);
    const chunkSize = 3 * 1024 * 1024;

    async function json(response) {
        const value = await response.json();
        if (!response.ok) throw Error(value.error || 'The workbench request failed.');
        return value;
    }

    window.fetch = async (input, init = {}) => {
        const url = new URL(typeof input === 'string' ? input : input.url, location.href);
        if (url.origin === location.origin && url.pathname === '/api/import-upload' && init.body instanceof Blob) {
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
        return rawFetch(input, init);
    };

    document.addEventListener('DOMContentLoaded', async () => {
        if (location.pathname === '/diagram.html') return;
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
                : 'Start a temporary session to import a feed.';
        }
        showSession(null);
        try { showSession((await json(await rawFetch('/api/session'))).expires_at); }
        catch (error) { status.textContent = error.message; }
        start.onclick = async () => {
            start.disabled = true;
            try { await json(await rawFetch('/api/session',{method:'POST'})); location.reload(); }
            catch (error) { status.textContent = error.message; }
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
