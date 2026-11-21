"""User-supplied, request-scoped OpenAI-compatible chat service adapter."""
import ipaddress,json,socket,urllib.parse,urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('The AI service redirected the request; use its direct endpoint.')

def complete(prompt,schema,service,*,output_limit=None,prompt_limit=None):
    if not isinstance(service,dict):raise ValueError('Connect an AI service in Admin before interpreting requests.')
    url=service.get('url','').strip();model=service.get('model','').strip();key=service.get('key','')
    if not model or not isinstance(key,str):raise ValueError('Enter a model and service credentials in Admin.')
    try:p=urllib.parse.urlsplit(url);port=p.port
    except (ValueError,TypeError):raise ValueError('Enter a valid AI service endpoint.')
    if p.username or p.password or p.fragment or not p.hostname or p.query:raise ValueError('Use an endpoint without embedded credentials or query parameters.')
    loopback=p.hostname in ('localhost','127.0.0.1','::1')
    if not ((p.scheme=='http' and loopback) or (p.scheme=='https' and not loopback)):
        raise ValueError('Use HTTPS for remote services or HTTP for localhost.')
    if not loopback:
        try:addresses=socket.getaddrinfo(p.hostname,port or 443,type=socket.SOCK_STREAM)
        except OSError:raise ValueError('The AI service hostname could not be resolved.')
        if not all(ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('Remote AI service must resolve to public addresses.')
    if not p.path.endswith('/chat/completions'):raise ValueError('Use the full /chat/completions endpoint URL.')
    body={'model':model,'messages':[{'role':'user','content':prompt}],'response_format':{'type':'json_schema','json_schema':{'name':'gtfs_interpretation','strict':True,'schema':schema}}}
    encoded=json.dumps(body,ensure_ascii=False).encode()
    if prompt_limit is not None and len(encoded)>prompt_limit:
        raise ValueError('This request contains too much transit context for included AI.')
    if output_limit is not None:
        body['max_completion_tokens']=output_limit
        encoded=json.dumps(body,ensure_ascii=False).encode()
    headers={'Content-Type':'application/json'}
    if key:headers['Authorization']='Bearer '+key
    req=urllib.request.Request(url,data=encoded,headers=headers,method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=120) as res:
            raw=res.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ValueError('The AI response exceeded the size limit.')
        response=json.loads(raw);content=response['choices'][0]['message']['content']
        return json.loads(content)
    except ValueError:raise
    except Exception as exc:raise ValueError('The AI service request failed. Check the endpoint, model, and credentials.') from exc
