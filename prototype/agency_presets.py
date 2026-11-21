"""Locally saved source endpoints; never stores feed data or credentials."""
import json,os,tempfile,urllib.parse,uuid,re
import workbench
with open(os.path.join(os.path.dirname(__file__),'builtin-agency-presets.json'),encoding='utf-8') as file:
 BUILTINS=json.load(file)
TYPES=('MTA New York Subway','GTFS Realtime alerts feed','Alerts webpage')
def path():return os.path.join(workbench.DATA_DIR,'agency-presets.json')
def read():
 try:
  with open(path(),encoding='utf-8') as f:data=json.load(f)
  return data if isinstance(data,list) else []
 except FileNotFoundError:return []
def list_all():return BUILTINS+read()
def validate_url(url,required=True):
 if not isinstance(url,str) or len(url)>2000:raise ValueError('Enter a valid public HTTPS URL.')
 url=url.strip()
 if not url and not required:return ''
 try:p=urllib.parse.urlsplit(url);port=p.port
 except ValueError:raise ValueError('Enter a valid HTTPS URL.')
 if p.scheme!='https' or not p.hostname or p.username or p.password or port not in (None,443) or p.fragment:raise ValueError('Use a public HTTPS URL without credentials or a fragment.')
 if any(re.search(r'(?:key|token|secret|password|auth|credential|signature)',k,re.I) for k,v in urllib.parse.parse_qsl(p.query,keep_blank_values=True)):raise ValueError('Remove credentials from the URL before saving the preset.')
 return url
def save(preset):
 name=preset.get('name');mode=preset.get('alert_type');id=preset.get('id')
 if not isinstance(name,str) or not name.strip() or len(name.strip())>100:raise ValueError('Enter an agency name of up to 100 characters.')
 if mode not in TYPES:raise ValueError('Choose a supported alert source type.')
 gtfs=validate_url(preset.get('gtfs_url',''));alert=validate_url(preset.get('alert_url',''),False)
 data=read()
 if any(x['id']==id for x in BUILTINS):raise ValueError('Built-in presets cannot be changed. Save a copy under another name.')
 if id and not any(x['id']==id for x in data):raise ValueError('Preset not found.')
 if not id:id='agency-'+uuid.uuid4().hex[:10]
 if any(x['name'].casefold()==name.strip().casefold() and x['id']!=id for x in data) or any(x['name'].casefold()==name.strip().casefold() for x in BUILTINS):raise ValueError('Choose a different agency name.')
 row={'id':id,'name':name.strip(),'gtfs_url':gtfs,'alert_type':mode,'alert_url':alert,'builtin':False}
 data=[x for x in data if x['id']!=id]+[row];write(data);return row
def delete(id):
 if any(x['id']==id for x in BUILTINS):raise ValueError('Built-in presets cannot be removed.')
 data=read();updated=[x for x in data if x['id']!=id]
 if len(data)==len(updated):raise ValueError('Preset not found.')
 write(updated)
def write(data):
 directory=os.path.dirname(path());os.makedirs(directory,exist_ok=True);fd,temp=tempfile.mkstemp(prefix='agency-presets-',dir=directory)
 try:
  with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
  os.replace(temp,path())
 finally:
  if os.path.exists(temp):os.unlink(temp)
