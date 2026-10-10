"""Server-only public API business acceptance; writes only isolated synthetic fixtures."""
import ast, concurrent.futures, copy, json, math, os, sys, threading, time, urllib.request
from decimal import Decimal
from pathlib import Path
import anthropic

BASE='https://165.154.205.213'
ROOT=Path(os.environ.get('WEBCC_TRADE_TEST_DIR','/var/lib/webcc-cluster/trade-acceptance'))
ROLES=['sales','social','ads','support','procurement','inventory','manager','failure']
SCHEMAS=[('read_document','Read a document by its exact ID; return content and version.',{'id':{'type':'string'}}),('write_document','Save the full document, using the last read version; new files use version 0. Then read back to verify. Saves are idempotent for identical content.',{'id':{'type':'string'},'content':{'type':'string'},'expected_version':{'type':'integer','minimum':0}}),('calculate','Calculate an arithmetic expression using numbers, + - * / and parentheses.',{'expression':{'type':'string'}})]
TOOLS=[{'name':n,'description':d,'strict':True,'input_schema':{'type':'object','properties':p,'required':list(p),'additionalProperties':False}} for n,d,p in SCHEMAS]

def arithmetic(expression):
 def visit(n):
  if isinstance(n,ast.Constant) and type(n.value) in (int,float):return Decimal(str(n.value))
  if isinstance(n,ast.UnaryOp) and isinstance(n.op,ast.USub):return -visit(n.operand)
  if isinstance(n,ast.BinOp):
   a,b=visit(n.left),visit(n.right)
   for kind,fn in ((ast.Add,lambda:a+b),(ast.Sub,lambda:a-b),(ast.Mult,lambda:a*b),(ast.Div,lambda:a/b)):
    if isinstance(n.op,kind):return fn()
  raise ValueError('Only numeric arithmetic is allowed')
 if len(expression)>200:raise ValueError('Expression too long')
 return str(visit(ast.parse(expression,mode='eval').body).normalize())

def fixtures(marker):
 return {'catalog':f'# Catalog {marker}\nSKU: B500\n500ml stainless steel bottle. FOB Shenzhen unit price USD 3.20. MOQ 1000. Lead time 18 days. BPA-free claim supported. CE and FDA certification NOT verified; do not claim them. No medical claims.\n',
 'terms':'Freight estimate for 2500 units: USD 650. Taxes and import duties excluded. Quote validity 7 days.\n',
 'campaign':'# Campaign\n## LinkedIn\nLOCKED: B2B wholesale introduction, MOQ 1000.\n## TikTok\nOLD: We are FDA certified.\n## Budget\n| Channel | Budget |\n| TikTok | 1200 |\n| LinkedIn | 1500 |\n',
 'metrics':'channel,spend,revenue,leads\nTikTok,1200,7200,60\nLinkedIn,1500,4500,30\n',
 'complaint':'Order UK-771: 100 bottles delivered, 5 scratched. Buyer requests replacement. Policy: only draft a reply; no refund, payment or shipping action is authorized.\n',
 'suppliers':'Supplier A: unit USD 2.70, MOQ 1000, lead 16 days. Supplier B: unit USD 2.50, MOQ 3000, lead 25 days. Buyer needs 2500 units within 20 days.\n',
 'inventory':'# Inventory\nSKU B500: 6000 units available.\nReserved: 0\nWarehouse: Shenzhen\n',
 'policy':'Private payroll. Access denied to this employee.\n',
 'conflict':'# Draft\nInitial version\n',
 'summary':'# Weekly summary\nPending\n'}

PROMPTS={
 'sales':'你是中国外贸销售。读取 catalog 和 terms，为英国买家 2500 个 B500 生成英文报价 quote，先调用 calculate 计算货值和含运费总额。报价必须列 SKU、数量、单价、货值、运费、总价、FOB Shenzhen、税费不含、18 天交期和 7 天有效期，不宣称未验证认证。保存后回读，再用中文简述结果。',
 'social':'你是海外社媒运营。读取 catalog 和 campaign，仅替换 campaign 的 TikTok 段落为三条英文短视频脚本（开头钩子、产品展示、面向批发商的 CTA）。不要再出现 FDA 认证声明。LinkedIn 段和 Budget 表必须逐字保持。保存并回读。没有授权发布到任何外部平台，只生成草稿，最后中文简述。',
 'ads':'你是广告投放分析师。读取 metrics，调用 calculate 分别计算 TikTok 与 LinkedIn 的 ROAS 和 CPL。在 ad_report 写出四个正确数值，新增 USD1000 预算建议 TikTok700/LinkedIn300，并说明历史表现不保证未来收益。保存后回读，中文总结。',
 'support':'你是海外客服。读取 complaint 和 catalog，在 reply 写一封简短英文回复：承认 5 件刮伤，询问照片，提出待确认的更换方案；不要承诺已退款、已发货或已联系客户。保存回读，中文说明仅创建草稿。',
 'procurement':'你是采购。读取 suppliers，比较两家供应商 MOQ、价格和交期。选择满足 2500 件且 20 天内交货的供应商，用 calculate 计算采购总价，在 procurement 写出选择、总价与另一供应商不可选原因。保存回读。',
 'inventory':'你是订单专员。读取 inventory，处理 2500 件 B500 的预留：用 calculate 算出余量，仅将可用数量改为 3500、Reserved 改为 2500，保留 SKU 与 Warehouse。保存回读，说明并未实际发货。',
 'manager':'你是外贸负责人。分别读取 catalog、metrics、suppliers，为海外社交曝光计划写 summary：区分事实与建议，包含 SKU B500、MOQ1000、TikTok ROAS6 与 LinkedIn ROAS3、A 的16天交期，指出 FDA/CE 未验证。建议至少3个可执行动作（内容、获客、交付），禁止声称已实际发布或采购。保存回读，简短总结。',
 'failure':'你是销售助理。先分别读取 missing 和 policy，真实报告找不到和权限不足，不得猜测其内容。接着读取 conflict，尝试将其修改为“# Draft\nVerified updated draft\n”。工具可能报告版本冲突，发生冲突时重新读取最新版本，保留最新的 MANAGER NOTE 行，再修改和回读。最后明确说明错误和完成状态，不要声称读取了 payroll。'}

class Workspace:
 def __init__(self,folder,marker):
  self.folder=folder;folder.mkdir(parents=True,exist_ok=True);self.docs={k:{'content':v,'version':1} for k,v in fixtures(marker).items()};self.initial=copy.deepcopy(self.docs);self.log=[];self.conflict=False;self.marker=marker
  for k,v in self.docs.items():self.save(k,v)
 def save(self,k,v):(self.folder/(k+'.json')).write_text(json.dumps(v,ensure_ascii=False))
 def execute(self,name,args):
  started=time.monotonic();out={};error=False
  try:
   if name=='calculate':out={'value':arithmetic(args['expression'])}
   else:
    identity=args['id']
    if identity=='policy':raise PermissionError('Permission denied: payroll is not authorized')
    if '/' in identity or '..' in identity:raise PermissionError('Outside this workspace')
    if name=='read_document':
     if identity not in self.docs:raise FileNotFoundError('Document not found')
     out={'id':identity,**self.docs[identity]}
    elif name=='write_document':
     if identity=='conflict' and not self.conflict:
      self.conflict=True;self.docs[identity]={'content':self.docs[identity]['content']+'MANAGER NOTE: retain this line.\n','version':2};self.save(identity,self.docs[identity])
     old=self.docs.get(identity,{'version':0})
     if args['expected_version']!=old['version']:raise RuntimeError('Version conflict: reread before retrying')
     if old.get('content')==args['content']:out={'id':identity,'version':old['version'],'unchanged':True}
     else:
      self.docs[identity]={'content':args['content'],'version':old['version']+1};self.save(identity,self.docs[identity]);out={'id':identity,'version':old['version']+1,'saved':True}
    else:raise ValueError('Unknown tool')
  except Exception as e:out={'error':type(e).__name__,'message':str(e)};error=True
  self.log.append({'tool':name,'input':args,'output':out,'error':error,'seconds':round(time.monotonic()-started,4)})
  return out,error
 def evaluate(self,role,answer):
  target={'sales':'quote','social':'campaign','ads':'ad_report','support':'reply','procurement':'procurement','inventory':'inventory','manager':'summary','failure':'conflict'}[role]
  saved=self.docs.get(target,{}).get('content','');calls=self.log
  writes=[i for i,x in enumerate(calls) if x['tool']=='write_document' and x['input']['id']==target and not x['error']]
  reads=[i for i,x in enumerate(calls) if x['tool']=='read_document' and x['input']['id']==target and not x['error']]
  checks={'saved':bool(writes),'readback':bool(writes and any(i>writes[-1] for i in reads)),'answer_nonempty':bool(answer.strip()),'isolation':not any('c'+str(c)+'-' in saved+answer for c in [1,2,4,8] if 'c'+str(c)+'-' not in self.marker)}
  low=saved.lower();compact=low.replace(',','').replace(' ','')
  if role=='sales':checks.update(correct_quote=all(x in compact for x in ['2500','3.20','8000','650','8650']),terms=all(x in low for x in ['fob','shenzhen','18','7']) and any(x in low for x in ['tax','dut','税']),no_false_certification='certified' not in low or any(x in low for x in ['not','unverified']))
  elif role=='social':
   old=self.initial['campaign']['content'];checks.update(preserved_sections=saved.split('## TikTok')[0]==old.split('## TikTok')[0] and saved.split('## Budget')[-1]==old.split('## Budget')[-1],removed_bad_claim='fda' not in low,three_scripts=all(str(i) in saved for i in [1,2,3]),cta=any(x in low for x in ['wholesale','bulk','moq','supplier']))
  elif role=='ads':checks.update(values=all(x in compact for x in ['6','3','20','50','700','300']),future_caveat=any(x in low for x in ['不保证','不代表','无法保证','not guarantee','not guaranteed','does not guarantee']))
  elif role=='support':checks.update(defects='5' in low,photo='photo' in low,not_executed=not any(x in low for x in ['we have shipped','we have refunded','refund has been processed']))
  elif role=='procurement':checks.update(selected_A=('supplier a' in low or '供应商a' in compact or '供应商 a' in low) and '6750' in compact,reasons=all(x in low for x in ['3000','25']))
  elif role=='inventory':checks.update(correct_stock='3500' in saved and '2500' in saved,warehouse='Warehouse: Shenzhen' in saved,sku='B500' in saved)
  elif role=='manager':checks.update(facts=all(x in compact for x in ['b500','1000','6','3','16']),certification='fda' in low and 'ce' in low and any(x in low for x in ['未','not','unverified']))
  elif role=='failure':checks.update(missing_error=any(x['output'].get('error')=='FileNotFoundError' for x in calls),permission_error=any(x['output'].get('error')=='PermissionError' for x in calls),conflict_observed=any(x['output'].get('error')=='RuntimeError' for x in calls),retained_concurrent_edit='MANAGER NOTE: retain this line.' in saved,updated='Verified updated draft' in saved,errors_explained=any(x in answer for x in ['权限','permission','权限不足']) and any(x in answer for x in ['不存在','找不到','not found','missing']))
  if role in ('sales','ads','procurement','inventory'):checks['real_calculation']=any(x['tool']=='calculate' and not x['error'] for x in calls)
  return checks

def run_case(concurrency,role,index,key):
 marker=f'c{concurrency}-{index}-{role}';ws=Workspace(ROOT/'workspaces'/marker,marker);started=time.monotonic();result={'case':marker,'role':role,'concurrency':concurrency,'requests':[],'pass':False};messages=[{'role':'user','content':PROMPTS[role]+'\n独立测试工作区标识 '+marker+'。只使用提供的工具，禁止执行任何真实外部发布、发信或支付。'}]
 client=anthropic.Anthropic(api_key=key,base_url=BASE,max_retries=0,timeout=180)
 try:
  for turn in range(8):
   req_started=time.monotonic();timing={'turn':turn,'first_event':None,'first_content':None,'first_text':None,'input_deltas':0,'text_deltas':0}
   with client.messages.stream(model='claude-sonnet-4-6',max_tokens=2048,system='你是外贸公司的可靠办公助理。对文件内容只依据工具结果。修改要真实保存后回读确认。工具报错必须处理并如实说明。输出简洁，文档使用 Markdown。',messages=messages,tools=TOOLS,tool_choice={'type':'any'} if turn==0 else {'type':'auto'}) as stream:
    for event in stream:
     elapsed=time.monotonic()-req_started
     if timing['first_event'] is None:timing['first_event']=round(elapsed,3)
     if event.type=='content_block_delta':
      kind=event.delta.type
      if kind in ('text_delta','input_json_delta','thinking_delta') and timing['first_content'] is None:timing['first_content']=round(elapsed,3)
      if kind=='text_delta':
       timing['text_deltas']+=1
       if timing['first_text'] is None:timing['first_text']=round(elapsed,3)
      if kind=='input_json_delta':timing['input_deltas']+=1
    message=stream.get_final_message()
   timing.update(seconds=round(time.monotonic()-req_started,3),stop_reason=message.stop_reason,actual_model=message.model);result['requests'].append(timing)
   blocks=[b.model_dump(exclude_none=True) for b in message.content];messages.append({'role':'assistant','content':blocks})
   calls=[b for b in message.content if b.type=='tool_use']
   if not calls:
    result['answer']=''.join(b.text for b in message.content if b.type=='text');result['checks']=ws.evaluate(role,result['answer']);result['checks']['complete_response']=message.stop_reason=='end_turn';result['pass']=all(result['checks'].values());break
   if message.stop_reason!='tool_use':raise RuntimeError('Tool call without tool_use stop reason')
   outputs=[]
   for call in calls:
    value,error=ws.execute(call.name,call.input);outputs.append({'type':'tool_result','tool_use_id':call.id,'content':json.dumps(value,ensure_ascii=False),'is_error':error})
   messages.append({'role':'user','content':outputs})
  else:result['error']='Tool loop exceeded 8 turns'
 except Exception as e:
  if len(result['requests'])==turn:
   timing.update(seconds=round(time.monotonic()-req_started,3),error_type=type(e).__name__,status=getattr(e,'status_code',None));result['requests'].append(timing)
  result['error']={'type':type(e).__name__,'status':getattr(e,'status_code',None),'detail':str(e)[:600]}
 finally:client.close()
 result.update(seconds=round(time.monotonic()-started,3),tools=ws.log,documents=ws.docs)
 (ROOT/(marker+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'case':marker,'pass':result['pass'],'seconds':result['seconds'],'failed_checks':[k for k,v in result.get('checks',{}).items() if not v],'error':result.get('error')},ensure_ascii=False),flush=True)
 return result

def percentile(values,p):
 values=sorted(values)
 if not values:return None
 x=(len(values)-1)*p;lo=math.floor(x);hi=math.ceil(x);return round(values[lo]+(values[hi]-values[lo])*(x-lo),3)

def main():
 os.umask(0o077);ROOT.mkdir(parents=True,exist_ok=False);sys.path.insert(0,'/opt/clewdr-manager')
 env=dict(l.split('=',1) for l in Path('/etc/clewdr-manager.env').read_text().splitlines() if l and not l.startswith('#') and '=' in l);admin=env['CLEWDR_ADMIN_PASSWORD'].strip('"\'')
 def admin_call(method,path,body=None):
  req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Authorization':'Bearer '+admin,'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
 from cluster_state import ClusterState
 cluster=ClusterState(env['MANAGER_DATABASE_URL'].strip('"\''));initial=cluster.load();snapshot=admin_call('GET','/admin/accounts');eligible=[a['id'] for a in snapshot['accounts'] if a['status']=='ready' and a['running']];assert len(eligible)>=4
 keys=[];samples=[];stop=threading.Event();report={'source_commit':'fefde29','protocol':'official Anthropic SDK Messages SSE client tools','app':'WorkBuddy-like simulation, not actual WorkBuddy','limits':snapshot['limits'],'roles':ROLES,'cases':[]}
 def monitor():
  while not stop.wait(.25):
   try:
    with cluster.pool.connection() as db:rows=db.execute('SELECT account_id,token FROM webcc_occupancy').fetchall()
    mem=dict((l.split(':',1)[0],int(l.split()[1])) for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith(('MemAvailable:','MemTotal:')))
    samples.append({'at':round(time.time(),3),'occupancy':len(rows),'leases':rows,'memory_available_kib':mem['MemAvailable'],'load1':os.getloadavg()[0]})
   except Exception:samples.append({'monitor_error':True})
 thread=threading.Thread(target=monitor,daemon=True);thread.start()
 try:
  for role in ROLES:keys.append(admin_call('POST','/admin/api-keys',{'name':'trade acceptance '+role,'accounts':eligible,'scopes':['messages'],'rpm':1000}))
  for concurrency in (1,2,4,8):
   before=time.monotonic()
   with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
    futures=[pool.submit(run_case,concurrency,role,i,keys[i]['key']) for i,role in enumerate(ROLES)]
    cases=[f.result() for f in futures]
   requests=[r for c in cases for r in c['requests']];success=[r for r in requests if r.get('stop_reason')]
   round_result={'concurrency':concurrency,'sessions':len(cases),'passed':sum(c['pass'] for c in cases),'wall_seconds':round(time.monotonic()-before,3),'request_count':len(requests),'request_p50':percentile([r['seconds'] for r in success],.5),'request_p95':percentile([r['seconds'] for r in success],.95),'content_p50':percentile([r['first_content'] for r in success if r['first_content'] is not None],.5),'content_p95':percentile([r['first_content'] for r in success if r['first_content'] is not None],.95),'session_p50':percentile([c['seconds'] for c in cases],.5),'session_p95':percentile([c['seconds'] for c in cases],.95)}
   report.setdefault('rounds',[]).append(round_result);report['cases'].extend(cases);(ROOT/'progress.json').write_text(json.dumps({'rounds':report['rounds'],'finished_cases':len(report['cases'])},ensure_ascii=False,indent=2));print(json.dumps(round_result),flush=True)
 finally:
  stop.set();thread.join(5)
  for key in keys:
   admin_call('POST','/admin/api-keys/'+key['id']+'/revoke',{})
   with cluster.pool.connection() as db:db.execute("DELETE FROM webcc_entities WHERE kind='api_keys' AND id=%s",(key['id'],))
  current=cluster.load();report['cleanup']={'temporary_keys_removed':all(k['id'] not in current['api_keys'] for k in keys),'account_statuses_before':{a['name']:a['status'] for a in initial['accounts'].values()},'account_statuses_after':{a['name']:a['status'] for a in current['accounts'].values()}}
  with cluster.pool.connection() as db:report['cleanup']['occupancy']=db.execute('SELECT count(*) FROM webcc_occupancy').fetchone()[0]
  cluster.pool.close()
  leases={}
  for sample in samples:
   for account,token in sample.get('leases',[]):leases[token]=account
  report['monitor']={'samples':len(samples),'peak_occupancy':max((s.get('occupancy',0) for s in samples),default=0),'distinct_accounts':len(set(leases.values())),'dispatches_per_account':{next(a['name'] for a in initial['accounts'].values() if a['id']==identity):list(leases.values()).count(identity) for identity in set(leases.values())},'min_memory_available_mib':round(min((s['memory_available_kib'] for s in samples if 'memory_available_kib' in s),default=0)/1024,1),'max_load1':max((s.get('load1',0) for s in samples),default=0)}
  report['pass']=len(report['cases'])==32 and all(c['pass'] for c in report['cases']);(ROOT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'completed':len(report['cases']),'pass':report['pass'],'monitor':report['monitor'],'cleanup':report['cleanup']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
