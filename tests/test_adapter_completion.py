import copy
import json
import time
import types
import unittest
from unittest.mock import patch
from history_state import PREFIX, cipher, seal, verify
from processing_cache import ProcessingCache
from web_files import FileProblem
from web_tools.streaming import ToolStream
from web_tools.api import prepare, complete
from message_stream import events


def manager():
    return types.SimpleNamespace(admin_key='private-test-key', shared_limits=None)


class StateAndCacheTests(unittest.TestCase):
    def test_thinking_owner_integrity_expiry_and_native_signature(self):
        m=manager();original={'type':'thinking','thinking':'Actual upstream summary','start_timestamp':None}
        signed=seal(m,'owner',original,'actual-model')
        self.assertTrue(signed['signature'].startswith(PREFIX))
        fields={'messages':[{'role':'assistant','content':[signed]}]}
        verify(m,'owner',fields)
        for owner,block in [('foreign',signed),('owner',{**signed,'thinking':'changed'})]:
            with self.assertRaises(FileProblem):verify(m,owner,{'messages':[{'content':[block]}]})
        token=cipher(m,'thinking').encrypt_at_time(b'{}',int(time.time())-86401).decode()
        with self.assertRaises(FileProblem):verify(m,'owner',{'messages':[{'content':[{**signed,'signature':PREFIX+token}]}]})
        native={**original,'signature':'native-opaque-signature'}
        self.assertEqual(seal(m,'owner',native,'actual-model'),native)

    def test_cache_isolation_copy_expiration_bound_and_failure(self):
        cache=ProcessingCache(manager());calls=[]
        def work():calls.append(1);return {'pages':['Original']}
        first=cache.memo('owner','pdf','same',work);first['pages'].append('mutated')
        self.assertEqual(cache.memo('owner','pdf','same',work),{'pages':['Original']})
        self.assertEqual(len(calls),1)
        cache.memo('foreign','pdf','same',work);self.assertEqual(len(calls),2)
        with patch('processing_cache.time.time',return_value=time.time()+301):cache.memo('owner','pdf','same',work)
        self.assertEqual(len(calls),3)
        for i in range(50):cache.memo('owner','pdf',i,lambda:{'ok':True})
        self.assertEqual(len(cache.local),32)
        def failed():raise ValueError('cannot parse')
        with self.assertRaises(ValueError):cache.memo('owner','pdf','bad',failed)


class IncrementalTests(unittest.TestCase):
    def request(self):
        return {'model':'actual-model','messages':[{'role':'user','content':'write'}],
            'tools':[{'name':'write','input_schema':{'type':'object','properties':{'a':{'type':'integer'},'items':{'type':'array'}},'required':['a'],'additionalProperties':False}}]}

    def stream(self,request=None):
        output=[];stream=ToolStream(manager(),'owner',request or self.request(),output.append)
        stream.upstream({'type':'message_start','message':{'id':'msg_real','model':'actual-model','usage':{'input_tokens':1,'output_tokens':0}}})
        return stream,output

    def test_parameters_arrive_before_generation_finishes(self):
        stream,output=self.stream()
        stream.upstream({'type':'content_block_delta','delta':{'type':'text_delta','text':'{"calls":[{"name":"write","input":{"a":37,'}})
        early=b''.join(output)
        self.assertIn(b'input_json_delta',early);self.assertIn(b'37',early)
        self.assertNotIn(b'content_block_stop',early);self.assertNotIn(b'message_stop',early)
        stream.upstream({'type':'content_block_delta','delta':{'type':'text_delta','text':'"items":[{"word":"北区\\n预算"},true,null,1.5]}}],"text":""}'}})
        stream.finish({'stop_reason':'tool_use','usage':{'output_tokens':5},'content':[
            {'type':'tool_use','name':'write','input':{'a':37,'items':[{'word':'北区\n预算'},True,None,1.5]}}]})
        frames=[json.loads(line[6:]) for line in b''.join(output).splitlines() if line.startswith(b'data: ')]
        raw=''.join(e.get('delta',{}).get('partial_json','') for e in frames)
        self.assertEqual(json.loads(raw),{'a':37,'items':[{'word':'北区\n预算'},True,None,1.5]})
        self.assertEqual(frames[-1]['type'],'message_stop');self.assertEqual(frames[0]['message']['model'],'actual-model')

    def test_text_unicode_escapes_and_real_thinking_state(self):
        stream,output=self.stream()
        stream.upstream({'type':'content_block_start','content_block':{'type':'thinking','thinking':''}})
        stream.upstream({'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'Actual summary'}})
        stream.upstream({'type':'content_block_stop'})
        for text in ['{"calls":[],"text":"北','区\\n预算','\\u0021"}']:
            stream.upstream({'type':'content_block_delta','delta':{'type':'text_delta','text':text}})
        stream.finish({'stop_reason':'end_turn','usage':{'output_tokens':5},'content':[{'type':'text','text':'北区\n预算!'}]})
        frames=[json.loads(line[6:]) for line in b''.join(output).splitlines() if line.startswith(b'data: ')]
        text=''.join(e.get('delta',{}).get('text','') for e in frames)
        self.assertEqual(text,'北区\n预算!')
        signature=next(e['delta']['signature'] for e in frames if e.get('delta',{}).get('type')=='signature_delta')
        verify(manager(),'owner',{'messages':[{'content':[{'type':'thinking','thinking':'Actual summary','signature':signature}]}]})

    def test_invalid_input_never_completes_tool_block(self):
        stream,output=self.stream()
        with self.assertRaises(ValueError):
            stream.upstream({'type':'content_block_delta','delta':{'type':'text_delta','text':'{"calls":[{"name":"write","input":{"a":"wrong"}}],"text":""}'}})
        self.assertNotIn(b'content_block_stop',b''.join(output))

    def test_required_choice_rejects_answer_before_emitting_content(self):
        request=self.request();request['tool_choice']={'type':'any'}
        stream,output=self.stream(request)
        stream.upstream({'type':'content_block_delta','delta':{'type':'text_delta','text':'{"calls":[],"text":"wrong"}'}})
        self.assertTrue(stream.failed);self.assertFalse(stream.emitted)

class NativeAndCitationTests(unittest.TestCase):
    def test_native_thinking_collection_and_signature_history(self):
        from native_events import NativeEvents
        m=manager();stream=NativeEvents(m,'owner')
        message={'id':'msg_actual','type':'message','role':'assistant','model':'actual-model','content':[{'type':'thinking','thinking':'Actual thought'},{'type':'text','text':'Answer'}],
                 'stop_reason':'end_turn','stop_sequence':None,'usage':{'input_tokens':1,'output_tokens':3}}
        raw=b''.join(events(message))
        for offset in range(0,len(raw),11):stream.feed(raw[offset:offset+11])
        self.assertTrue(stream.finished);self.assertEqual(stream.result['content'][1]['text'],'Answer')
        verify(m,'owner',{'messages':[{'content':stream.result['content']}]})
        self.assertTrue(stream.result['content'][0]['signature'].startswith(PREFIX))

    def test_web_search_sources_are_real_and_owner_bound(self):
        from native_events import NativeEvents,restore
        stream=NativeEvents(manager(),'owner')
        block={'type':'tool_result','tool_use_id':'srv1','name':'web_search','content':[{'type':'knowledge','url':'https://example.com/page','title':'Actual title'}]}
        packet=stream.frame({'type':'content_block_start','index':0,'content_block':block})[0]
        value=json.loads(packet.split(b'data: ')[1]);result=value['content_block']
        self.assertEqual(result['type'],'web_search_tool_result');self.assertEqual(result['content'][0]['url'],'https://example.com/page')
        fields={'messages':[{'content':[result]}]};restore(manager(),'owner',copy.deepcopy(fields))
        with self.assertRaises(FileProblem):restore(manager(),'foreign',fields)

    def test_cited_document_and_tool_return_share_source_validation(self):
        fields={'model':'actual-model','max_tokens':2048,'messages':[{'role':'user','content':[
            {'type':'document','title':'Budget','source':{'type':'text','data':'North budget is 37.'},'citations':{'enabled':True}},
            {'type':'text','text':'Use calculator then cite budget.'}]}],
            'tools':[{'name':'calculator','strict':True,'input_schema':{'type':'object','properties':{'a':{'type':'integer'}},'required':['a']}}]}
        request,body=prepare(json.dumps(fields),standard=True,owner='owner',cache=ProcessingCache(manager()))
        self.assertIn('_citation_sources',request);self.assertEqual(request['tools'],fields['tools'])
        answer={'answer':'North budget is 37.','quotes':[{'document_index':0,'page':1,'start':0,'quote':'North budget is 37.'}]}
        envelope={'calls':[],'text':json.dumps(answer)}
        raw=json.dumps({'content':[{'type':'text','text':json.dumps(envelope)}],'model':'actual-model','usage':{'input_tokens':1,'output_tokens':2}})
        result=complete(raw,request);citation=result['content'][0]['citations'][0]
        self.assertEqual(citation['start_char_index'],0);self.assertEqual(citation['end_char_index'],19)

class GatewayIncrementalTests(unittest.TestCase):
    from test_web_gateway import WebGatewayTests as Base
    setUp=Base.setUp
    tearDown=Base.tearDown
    request=Base.request
    assert_idle=Base.assert_idle

    def test_socket_parameters_arrive_while_upstream_is_still_generating(self):
        import http.client,threading
        from test_web_gateway import Worker,TOOLS
        from message_stream import event
        finish=threading.Event()
        def slow(worker):
            worker.rfile.read(int(worker.headers['Content-Length']))
            worker.send_response(200);worker.send_header('Content-Type','text/event-stream');worker.send_header('Connection','close');worker.end_headers()
            frames=[event('message_start',message={'id':'msg_real','type':'message','role':'assistant','model':'actual-model','usage':{'input_tokens':1,'output_tokens':0}}),
                event('content_block_start',index=0,content_block={'type':'text','text':''}),
                event('content_block_delta',index=0,delta={'type':'text_delta','text':'{"calls":[{"name":"read","input":{"id":"A"'})]
            for frame in frames:worker.wfile.write(frame);worker.wfile.flush()
            finish.wait(4)
            for frame in [event('content_block_delta',index=0,delta={'type':'text_delta','text':'}}],"text":""}'}),event('content_block_stop',index=0),
                          event('message_delta',delta={'stop_reason':'end_turn','stop_sequence':None},usage={'output_tokens':10}),event('message_stop')]:
                worker.wfile.write(frame);worker.wfile.flush()
            worker.close_connection=True
        with patch.object(Worker,'do_POST',slow):
            c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
            body={'model':'claude-sonnet-4-6','max_tokens':2048,'stream':True,'tools':TOOLS,'messages':[{'role':'user','content':'Read A'}]}
            try:
                c.request('POST','/v1/messages',json.dumps(body),{'Authorization':'Bearer api-password','Content-Type':'application/json'})
                r=c.getresponse();self.assertEqual(r.status,200)
                packets=[]
                while True:
                    line=r.readline();packets.append(line)
                    if b'input_json_delta' in line:break
                self.assertFalse(finish.is_set());finish.set()
                packets.append(r.read());self.assertIn(b'message_stop',b''.join(packets))
            finally:finish.set();c.close()
        self.assert_idle()

    def test_native_nonstream_preserves_real_thinking_and_stop_reason(self):
        from test_web_gateway import Worker
        message={'id':'msg_real','type':'message','role':'assistant','model':'actual-model','content':[{'type':'thinking','thinking':'Actual summary'},{'type':'text','text':'Hi'}],
                 'stop_reason':'max_tokens','stop_sequence':None,'usage':{'input_tokens':1,'output_tokens':3}}
        account=self.manager.get_account(self.identity)
        Worker.replies['Bearer '+account['key']]=(200,b''.join(events(message)),'text/event-stream')
        status,_,raw=self.request('POST','/v1/messages',{'model':'claude-sonnet-4-6','max_tokens':8,'messages':[{'role':'user','content':'Hi'}]})
        result=json.loads(raw);self.assertEqual(status,200);self.assertEqual(result['stop_reason'],'max_tokens')
        self.assertEqual(result['content'][1]['text'],'Hi');self.assertTrue(result['content'][0]['signature'].startswith(PREFIX));self.assert_idle()

    def test_schema_failure_corrects_before_any_client_tool_executes(self):
        from test_web_gateway import Worker,TOOLS,raw_output
        second=self.manager.add({'name':'second','sessionKey':'sk-ant-sid02-'+'B'*100+'-ABCDEF'+'AA','proxy':'127.0.0.1:1080:u:p'})
        first=self.manager.get_account(self.identity)
        Worker.replies['Bearer '+first['key']]=(200,raw_output({'calls':[{'name':'read','input':{'id':'invalid'}}],'text':''}))
        Worker.replies['Bearer '+self.manager.get_account(second)['key']]=(200,raw_output())
        body={'model':'claude-sonnet-4-6','max_tokens':2048,'tools':TOOLS,'messages':[{'role':'user','content':'Read A'}]}
        status,_,raw=self.request('POST','/v1/messages',body)
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['content'][0]['input'],{'id':'A'})
        self.assertEqual(len(Worker.seen),2)
        correction=json.loads(Worker.seen[-1]['body'])['messages'][-1]['content']
        feedback,_=json.JSONDecoder().raw_decode(correction.split('failed validation: ',1)[1])
        self.assertEqual(feedback['path'],['calls',0,'input','id']);self.assertEqual(feedback['rule'],'enum')
        self.assertEqual(feedback['issues'][0]['path'],feedback['path'])
        self.assertEqual(first['status'],'ready');self.assert_idle()

class SourceGuards(unittest.TestCase):
    def test_source_fetch_never_falls_back_to_direct_and_rejects_private_dns(self):
        from source_text import fetch,target
        with patch.dict('os.environ',{'MANAGER_TOOLS_PROXY':'','MANAGER_E2B_PROXY':''}),self.assertRaises(FileProblem):fetch('https://example.com/')
        with patch('source_text.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),self.assertRaises(FileProblem):target('https://example.com/')
        for url in ('http://example.com','https://user:pass@example.com','https://example.com:9443'):
            with self.assertRaises(FileProblem):target(url)

class ArtifactAndSelectionTests(unittest.TestCase):
    def test_artifact_paths_have_one_root_and_input_is_not_mutated(self):
        from run_tasks import validate,plan
        original={'code':'print(120)','outputs':['output/total.txt'],'files':[{'file_id':'file_id','path':'input/source.txt'}]}
        m=types.SimpleNamespace(tasks=None,files=types.SimpleNamespace(get=lambda owner,identity:('id','owner','source.txt','text/plain',0,None,b'120')))
        body=validate(original,m,'owner');self.assertEqual(body['outputs'],['total.txt']);self.assertEqual(body['files'][0]['path'],'source.txt')
        self.assertEqual(original['files'][0]['path'],'input/source.txt')
        item={'owner':'owner','messages':[{'role':'user','content':'Calculate'}],'tools':[],'skill_snapshots':[]}
        def inference(*args):
            request = args[2]
            self.assertEqual(request['tools'], [])
            self.assertEqual(request['tool_choice'], {'type': 'none'})
            self.assertEqual(request['output_config']['format']['type'], 'json_schema')
            return {'model':'actual-model','content':[{'type':'text','text':json.dumps({'code':'print(120)','outputs':['output/total.txt']})}]}
        self.assertEqual(plan(m,item,inference),('print(120)',['total.txt']))

    def test_execution_plan_rejects_missing_code_without_executing(self):
        from run_tasks import plan
        item = {'owner': 'owner', 'messages': [{'role': 'user', 'content': 'Calculate'}], 'tools': [], 'skill_snapshots': []}
        for value in ({'outputs': []}, {'code': 120, 'outputs': []}, {'code': 'print(120)', 'outputs': [], 'run_now': True}):
            with self.subTest(value=value), self.assertRaises(Exception):
                plan(types.SimpleNamespace(), copy.deepcopy(item), lambda *args: {'content': [{'type': 'text', 'text': json.dumps(value)}]})

    def test_disabled_code_execution_uses_no_sandbox_or_runtime_permission(self):
        from messages_api import dispatch
        fields={'model':'actual-model','messages':[{'role':'user','content':'Answer directly'}],
                'tools':[{'type':'code_execution_20260521','name':'code_execution'}],'tool_choice':{'type':'none'}}
        handler=types.SimpleNamespace(caller_key='owner',manager=types.SimpleNamespace(tasks=None))
        with patch('web_tools.gateway.forward') as forward:
            self.assertTrue(dispatch(handler,fields));forward.assert_called_once()
            request=json.loads(forward.call_args.kwargs['raw']);self.assertEqual(request['tools'],[]);self.assertEqual(request['tool_choice'],{'type':'none'})

    def test_latest_skill_is_resolved_to_an_owned_immutable_version(self):
        from skill_bundles import snapshot
        calls=[]
        store=types.SimpleNamespace(skills=lambda owner,identity:[{'version':'version_one'}],
            skill_version=lambda owner,identity,version:(calls.append((owner,identity,version)) or {'name':'totals','instructions':'Sum','files':{'SKILL.md':'Sum'}}))
        saved=snapshot(store,'owner',[{'skill_id':'skill_one','version':'latest'}])
        self.assertEqual(calls,[('owner','skill_one','version_one')]);self.assertEqual(saved[0]['version'],'version_one')
