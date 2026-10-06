import subprocess,json,shutil,socket,time,tomllib,http.client,tempfile
from pathlib import Path
from urllib.parse import urlsplit

def run(args,check=True):
 r=subprocess.run(args,capture_output=True,text=True,timeout=30)
 if check and r.returncode:raise RuntimeError('command failed '+args[0])
 return r

def request(port,key,path,body=None,timeout=35):
 c=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout);t=time.monotonic()
 try:
  c.request('POST' if body else 'GET',path,json.dumps(body).encode() if body else None,{'Authorization':'Bearer '+key,'Content-Type':'application/json','anthropic-version':'2023-06-01'})
  r=c.getresponse();raw=r.read();data=json.loads(raw)
  result={'status':r.status,'seconds':round(time.monotonic()-t,2)}
  if r.status>=400:
   error=data.get('error',{});result['error_type']=error.get('type') if isinstance(error,dict) else 'unknown'
   msg=json.dumps(data)
   result['classification']='free_account' if 'free' in msg.lower() else 'no_cookie' if 'cookie' in msg.lower() else 'upstream_error'
  else:
   result.update(model=data.get('model'),stop_reason=data.get('stop_reason'),blocks=[b.get('type') for b in data.get('content',[])],text=''.join(b.get('text','') for b in data.get('content',[]))[:500],usage=data.get('usage'))
  return result
 except Exception as e:return {'transport_error':type(e).__name__,'seconds':round(time.monotonic()-t,2)}
 finally:c.close()

registry=Path('/var/lib/clewdr-manager/registry.json')
if not registry.exists():raise SystemExit('Run only on the deployment server as root')
for name in ('webcc-depth-worker','webcc-depth-net'):
 if run(['docker','inspect',name],False).returncode==0:raise SystemExit('Existing test resource: '+name)
initial=registry.read_bytes();accounts=json.loads(initial)['accounts'];report=[]
net='webcc-depth-net';worker='webcc-depth-worker';base=Path(tempfile.mkdtemp(prefix='webcc-depth-'));rules=[]
try:
 run(['docker','network','create',net]);ni=json.loads(run(['docker','network','inspect',net]).stdout)[0];assert not ni.get('EnableIPv6'), 'IPv6 network requires separate egress rules'
 ip=ni['IPAM']['Config'][0]['Gateway'].rsplit('.',1)[0]+'.10'
 for a in accounts.values():
  if a['status']!='ready':continue
  dest=base/'worker';dest.mkdir(mode=0o700)
  for p in Path(a['directory']).glob('*.toml'):shutil.copy2(p,dest/p.name)
  cfg=tomllib.loads((dest/'clewdr.toml').read_text());proxy=urlsplit(cfg['proxy']);port=proxy.port;ips=sorted({x[4][0] for x in socket.getaddrinfo(proxy.hostname,port,socket.AF_INET,socket.SOCK_STREAM)})
  run(['docker','create','--name',worker,'--network',net,'--ip',ip,'--log-driver','none','--memory','256m','--cpus','1','--pids-limit','64','-p','127.0.0.1:19092:8484','-v',str(dest)+':/etc/clewdr',a['image']])
  rules=[['-s',ip,'-j','DROP']]+[['-s',ip,'-p',proto,'--dport','53','-j','ACCEPT'] for proto in ['udp','tcp']]+[['-s',ip,'-d',dst,'-p','tcp','--dport',str(port),'-j','ACCEPT'] for dst in ips]+[['-s',ip,'-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT']]
  for rule in rules:run(['iptables','-I','DOCKER-USER','1']+rule)
  run(['docker','start',worker]);ready=None
  for _ in range(15):
   ready=request(19092,cfg['password'],'/v1/models',timeout=2)
   if ready.get('status')==200:break
   time.sleep(1)
  row={'account':a['name'],'readiness':ready,'proxy_only_firewall':True}
  print(json.dumps({'account':a['name'],'stage':'ready','result':ready}),flush=True)
  if ready.get('status')==200:
   body={'model':'claude-sonnet-4-6','max_tokens':2048,'stream':False,'messages':[{'role':'user','content':'Reply with exactly Hello.'}]}
   row['web_text']=request(19092,cfg['password'],'/v1/messages',body,timeout=45)
   print(json.dumps({'account':a['name'],'stage':'web_text','result':row['web_text']}),flush=True)
   
   # Check text and structured client tools before native failure can retire the copied cookie.
   body['tools']=[{'name':'read_document','description':'Read the test document','input_schema':{'type':'object','properties':{'id':{'type':'string'}},'required':['id']}}]
   body['messages']=[{'role':'user','content':'Use read_document with id test-001 to read the document. Do not invent its contents.'}]
   row['web_tool']=request(19092,cfg['password'],'/v1/messages',body,timeout=45)
   print(json.dumps({'account':a['name'],'stage':'web_tool','result':row['web_tool']}),flush=True)
  if ready.get('status')==200:
   row['native']=request(19092,cfg['password'],'/code/v1/messages',{'model':'claude-sonnet-4-6','max_tokens':2048,'messages':[{'role':'user','content':'Reply Hello.'}]})
   after=tomllib.loads((dest/'clewdr.toml').read_text())
   row['wasted_reasons']=[{k:v for k,v in item.items() if k in ('reason','disabled','is_pro')} for item in after.get('wasted_cookie',[]) if isinstance(item,dict)]
   print(json.dumps({'account':a['name'],'stage':'native','result':row['native'],'wasted_reasons':row['wasted_reasons']}),flush=True)
  report.append(row)
  run(['docker','rm','-f',worker],False)
  for rule in reversed(rules):run(['iptables','-D','DOCKER-USER']+rule,False)
  rules=[];shutil.rmtree(dest)
finally:
 run(['docker','rm','-f',worker],False)
 for rule in reversed(rules):run(['iptables','-D','DOCKER-USER']+rule,False)
 run(['docker','network','rm',net],False);shutil.rmtree(base,ignore_errors=True)
 out={'accounts':report,'production_registry_unchanged':registry.read_bytes()==initial,'temporary_resources_removed':run(['docker','inspect',worker],False).returncode!=0 and run(['docker','network','inspect',net],False).returncode!=0 and not base.exists()}
 p=Path('/var/lib/clewdr-manager/account-depth-test.json');p.write_text(json.dumps(out,ensure_ascii=False,indent=2));p.chmod(0o600)
 print(json.dumps({'final':out}),flush=True)
