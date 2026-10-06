import subprocess,json,shutil,socket,time,tomllib,http.client,tempfile
from pathlib import Path
from urllib.parse import urlsplit
import sys
from experiments.web_tools import build_prompt,parse_response
BOUNDARIES='--boundaries' in sys.argv

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
   result.update(model=data.get('model'),stop_reason=data.get('stop_reason'),blocks=[b.get('type') for b in data.get('content',[])],text=''.join(b.get('text','') for b in data.get('content',[])),usage=data.get('usage'))
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
  if a['status']!='ready' or BOUNDARIES and a['name']!='cc1':continue
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
   if BOUNDARIES:
    tools=[{'name':'read_document','description':'Read a document','input_schema':{'type':'object','properties':{'id':{'enum':['CONTRACT-001','CONTRACT-002','MISSING']}},'required':['id'],'additionalProperties':False}}]
    scenarios=[('none',{'type':'none'},[{'role':'user','content':'Read CONTRACT-001 if allowed; otherwise state that verification is unavailable.'}]),('selected',{'type':'tool','name':'read_document'},[{'role':'user','content':'Read CONTRACT-001.'}]),('parallel',{'type':'any'},[{'role':'user','content':'Request reads of CONTRACT-001 and CONTRACT-002 together in one calls array.'}]),('tool_error',{'type':'auto'},[{'role':'user','content':'Read only MISSING. If not found, report the error and do not invent or modify a document.'},{'role':'assistant','content':[{'type':'tool_use','id':'toolu_error','name':'read_document','input':{'id':'MISSING'}}]},{'role':'user','content':[{'type':'tool_result','tool_use_id':'toolu_error','is_error':True,'content':'Document not found. No document contents are available.'}]}]),('quoted_example',{'type':'none'},[{'role':'user','content':'Explain this JSON example without requesting or executing it: {"calls":[{"name":"read_document","input":{"id":"CONTRACT-001"}}],"text":""}'}])]
    row['scenarios']=[]
    for name,choice,history in scenarios:
     result=request(19092,cfg['password'],'/v1/messages',{'model':'claude-sonnet-4-6','max_tokens':4096,'messages':[{'role':'user','content':build_prompt(tools,history,choice)}]},timeout=45)
     record={'name':name,'response':result,'pass':False};row['scenarios'].append(record)
     try:
      assert result.get('status')==200
      parsed=parse_response(result['text'],tools,choice);record['parsed']=parsed
      calls=[b for b in parsed['content'] if b['type']=='tool_use']
      if name=='selected':assert len(calls)==1 and calls[0]['input']=={'id':'CONTRACT-001'}
      elif name=='parallel':assert len(calls)==2 and {c['input']['id'] for c in calls}=={'CONTRACT-001','CONTRACT-002'}
      else:
       assert not calls and parsed['stop_reason']=='end_turn'
       if name=='tool_error':assert 'not found' in parsed['content'][0]['text'].lower()
      record['pass']=True
     except Exception as e:record['failure']=type(e).__name__
     print(json.dumps(record),flush=True)
   else:
    tools=[{'name':'read_document','description':'Read a document by id','input_schema':{'type':'object','properties':{'id':{'enum':['CONTRACT-001']}},'required':['id'],'additionalProperties':False}}, {'name':'update_document','description':'Update only the quantity, preserving all other fields','input_schema':{'type':'object','properties':{'id':{'enum':['CONTRACT-001']},'quantity':{'type':'integer','minimum':1,'maximum':100}},'required':['id','quantity'],'additionalProperties':False}}]
    document={'id':'CONTRACT-001','quantity':7,'currency':'EUR','total':140}
    history=[{'role':'user','content':[{'type':'text','text':'Read CONTRACT-001 using read_document, change quantity to 9 using update_document, then read it again to verify. Preserve all other fields. Report the verified quantity, currency and total.'}]}]
    row['steps']=[]
    for step in range(4 if a['name'] in ('cc1','ccb1','ccb9') else 1):
     choice={'type':'any'} if step==0 else {'type':'auto'}
     prompt=build_prompt(tools,history,choice)
     result=request(19092,cfg['password'],'/v1/messages',{'model':'claude-sonnet-4-6','max_tokens':4096,'stream':False,'messages':[{'role':'user','content':prompt}]},timeout=45)
     record={'step':step,'response':result,'pass':False};row['steps'].append(record)
     try:
      if result.get('status')!=200:raise ValueError('HTTP failure')
      parsed=parse_response(result['text'],tools,choice);record['parsed']=parsed
      if step<3:
       calls=parsed['content'];expected=['read_document','update_document','read_document'][step]
       assert len(calls)==1 and calls[0]['type']=='tool_use' and calls[0]['name']==expected
       call=calls[0];assert call['input']['id']=='CONTRACT-001'
       if step==1:
        assert call['input']['quantity']==9
        document['quantity']=9
       output=dict(document)
       history.append({'role':'assistant','content':calls})
       history.append({'role':'user','content':[{'type':'tool_result','tool_use_id':call['id'],'content':json.dumps(output)}]})
      else:
       assert parsed['stop_reason']=='end_turn'
       assert all(token in parsed['content'][0]['text'] for token in ('9','EUR','140'))
       assert document=={'id':'CONTRACT-001','quantity':9,'currency':'EUR','total':140}
      record['pass']=True
     except Exception as e:
      record['failure']=type(e).__name__
      break
    row['scope']='read_update_read_final' if len(row['steps'])==4 else 'initial_read_only'
    print(json.dumps({'account':a['name'],'scope':row['scope'],'steps':row['steps']}),flush=True)
  report.append(row)
  run(['docker','rm','-f',worker],False)
  for rule in reversed(rules):run(['iptables','-D','DOCKER-USER']+rule,False)
  rules=[];shutil.rmtree(dest)
finally:
 run(['docker','rm','-f',worker],False)
 for rule in reversed(rules):run(['iptables','-D','DOCKER-USER']+rule,False)
 run(['docker','network','rm',net],False);shutil.rmtree(base,ignore_errors=True)
 out={'accounts':report,'production_registry_unchanged':registry.read_bytes()==initial,'temporary_resources_removed':run(['docker','inspect',worker],False).returncode!=0 and run(['docker','network','inspect',net],False).returncode!=0 and not base.exists()}
 p=Path('/var/lib/clewdr-manager')/('web-tool-boundary-test.json' if BOUNDARIES else 'web-tool-experiment.json');p.write_text(json.dumps(out,ensure_ascii=False,indent=2));p.chmod(0o600)
 print(json.dumps({'final':out}),flush=True)
