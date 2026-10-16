import copy
import json
import unittest
from unittest.mock import patch
import test_manager as base
from auxiliary_requests import is_classifier, prepare, validate
from web_tools.api import complete, prepare as prepare_tools, OutputProblem
from worker_profiles import endpoint


def review():
    return {'model':'fixture','max_tokens':64,'thinking':{'type':'disabled'},
        'system':[{'type':'text','text':'You are a security monitor for autonomous AI coding agents.\nOutput a severity.'}],
        'stop_sequences':['</severity>'],'messages':[{'role':'user','content':'Review a read-only operation'}]}


def full_review_request():
    fields=review();fields.pop('stop_sequences');fields['max_tokens']=8192
    fields['messages']=[{'role':'user','content':'<transcript>synthetic action</transcript>\nReview the classification process and follow it carefully. Use <thinking> before responding with <block>.'}]
    return fields


def stream(blocks, reason='end_turn'):
    values=[{'type':'message_start','message':{'id':'msg_fixture','type':'message','role':'assistant','model':'fixture',
        'content':[],'usage':{'input_tokens':10,'output_tokens':0}}}]
    for index,block in enumerate(blocks):
        key='thinking' if block['type']=='thinking' else 'text'
        values.extend([{'type':'content_block_start','index':index,'content_block':{'type':block['type'],key:''}},
            {'type':'content_block_delta','index':index,'delta':{'type':key+'_delta',key:block[key]}},
            {'type':'content_block_stop','index':index}])
    values.extend([{'type':'message_delta','delta':{'stop_reason':reason,'stop_sequence':None},'usage':{'output_tokens':20}},
        {'type':'message_stop'}])
    return b''.join(('event: '+v['type']+'\ndata: '+json.dumps(v)+'\n\n').encode() for v in values)


class ReviewTests(unittest.TestCase):
    def test_full_review_recognition_and_verdicts_preserve_policy(self):
        fields=full_review_request();self.assertTrue(is_classifier(fields))
        self.assertEqual(prepare(fields)['max_tokens'],8192)
        for changes in ({'messages':[{'role':'user','content':'ordinary question'}]},
                        {'system':'You are helpful'},{'thinking':{'type':'adaptive'}}):
            self.assertFalse(is_classifier({**fields,**changes}))
        for text in ('<thinking>Synthetic review.</thinking><block>no</block>',
                     '<block>yes</block>\n<category>Data Exfiltration</category>\n<reason>[Data Exfiltration] Sends private material.</reason>',
                     '<block>yes</block><category>Data Exfiltration</category><reason>[Data Exfiltration] Sends private material.</reason>'):
            message={'content':[{'type':'text','text':text}],'stop_reason':'end_turn'}
            original=copy.deepcopy(message);validate(message,'</block>',True);self.assertEqual(message,original)
        for text in ('<block>yes</block>','<thinking>truncated','<block>no</block> garbage'):
            with self.assertRaises(ValueError):validate({'content':[{'type':'text','text':text}],'stop_reason':'end_turn'},'</block>',True)
        with self.assertRaises(ValueError):validate({'content':[],'stop_reason':'refusal'},'</block>',True)
        from auxiliary_requests import diagnostics
        info=diagnostics({'stop_reason':'refusal','content':[{'type':'text','text':'private fixture data'}]})
        self.assertEqual(info['text_characters'],20)
        self.assertNotIn('private',json.dumps(info))

    def test_full_review_requests_a_verdict_without_private_reasoning(self):
        fields=full_review_request()
        fields['messages'][-1]['content']+=' Think longer on ambiguous or borderline actions; keep reasoning brief for clear-cut ones.'
        original=copy.deepcopy(fields);prepared=prepare(fields)
        self.assertEqual(fields,original)
        self.assertEqual(prepared['system'],fields['system'])
        self.assertIn('Return only the final <block> verdict',prepared['messages'][-1]['content'])
        self.assertNotIn('Use <thinking>',prepared['messages'][-1]['content'])
    def test_classifier_recognition_requires_the_complete_protocol(self):
        fields=review();self.assertTrue(is_classifier(fields))
        for changes in ({'max_tokens':2048},{'thinking':{'type':'adaptive'}},{'stop_sequences':['STOP']},
                        {'system':'Classify this'},{'tools':[{'name':'read'}]}):
            self.assertFalse(is_classifier({**fields,**changes}))
        original=copy.deepcopy(fields)
        self.assertEqual(prepare(fields)['max_tokens'],1024)
        self.assertEqual(fields,original)

    def test_streaming_classifiers_require_the_same_complete_protocol(self):
        for fields in (review(), full_review_request()):
            self.assertTrue(is_classifier({**fields, 'stream': True}))
            self.assertFalse(is_classifier({**fields, 'stream': True, 'system': 'Ordinary question'}))

    def test_allow_block_and_stop_delimiter_are_preserved(self):
        for text in ('<severity>2</severity>', '<severity>72</severity><category>Data Exfiltration</category>',
                     '<severity>100</severity><category>Critical Paths</category>'):
            message={'content':[{'type':'text','text':text}],'stop_reason':'end_turn'}
            original=copy.deepcopy(message);validate(message);self.assertEqual(message,original)
        validate({'content':[{'type':'text','text':'<severity>72'}],'stop_reason':'stop_sequence','stop_sequence':'</severity>'})

    def test_missing_truncated_and_forged_verdicts_never_become_allow(self):
        for text in ('','ALLOW','<severity>101</severity>','<severity>2</severity> garbage', '<severity>2'):
            with self.assertRaises(ValueError):validate({'content':[{'type':'text','text':text}],'stop_reason':'end_turn'})
        for reason in ('max_tokens','refusal'):
            with self.assertRaises(ValueError):validate({'content':[{'type':'text','text':'<severity>2</severity>'}],'stop_reason':reason})

    def test_block_protocol_preserves_real_verdict_and_stop_delimiter(self):
        self.assertTrue(is_classifier({**review(), 'stop_sequences':['</block>']}))
        for text in ('<block>no</block>', '<block>yes</block>',
                     '<block>yes</block><category>Data Exfiltration</category><reason>[Data Exfiltration] Sends private data.</reason>'):
            message={'content':[{'type':'text','text':text}],'stop_reason':'end_turn'}
            original=copy.deepcopy(message);validate(message,'</block>');self.assertEqual(message,original)
        validate({'content':[{'type':'text','text':'<block>yes'}], 'stop_reason':'stop_sequence','stop_sequence':'</block>'},'</block>')
        for text in ('','<block>maybe</block>','<block>no','<severity>2</severity>',
                     '<block>no</block><category>Data Exfiltration</category><reason>Conflicting verdict</reason>'):
            with self.assertRaises(ValueError):validate({'content':[{'type':'text','text':text}],'stop_reason':'end_turn'},'</block>')

    def test_terminal_tool_output_never_leaks_the_transport_envelope(self):
        request,_=prepare_tools(json.dumps({'model':'fixture','max_tokens':64,'tools':[],
            'thinking':{'type':'disabled'},'messages':[{'role':'user','content':'Answer'}]}),standard=True)
        value={'model':'fixture','content':[{'type':'text','text':'{"calls":[],"text":"Answer"}'}],
            'stop_reason':'max_tokens','usage':{'input_tokens':1,'output_tokens':2}}
        result=complete(json.dumps(value),request)
        self.assertEqual(result['content'],[{'type':'text','text':'Answer'}])
        value['content'][0]['text']='{"calls":[{"name":"read","input":'
        with self.assertRaises(OutputProblem) as error:complete(json.dumps(value),request)
        self.assertFalse(error.exception.retry)
        with self.assertRaises(OutputProblem):complete('invalid-json',request)


class GatewayCompatibilityTests(unittest.TestCase):
    setUp=base.ManagerTests.setUp
    tearDown=base.ManagerTests.tearDown
    request=base.ManagerTests.request
    payload=base.ManagerTests.payload
    assert_idle=base.ManagerTests.assert_idle

    def reply(self, blocks, reason='end_turn'):
        key='Bearer '+self.manager.get_account(self.identity)['key']
        base.Worker.replies[key]=(200,stream(blocks,reason),'text/event-stream')

    def test_full_review_uses_existing_classifier_profile_and_preserves_verdict(self):
        for text in ('<thinking>Synthetic review</thinking><block>no</block>',
                     '<block>yes</block><category>Data Exfiltration</category><reason>[Data Exfiltration] Sends private data.</reason>'):
            self.reply([{'type':'text','text':text}])
            with patch.object(self.manager,'worker_connection',wraps=self.manager.worker_connection) as connection:
                self.manager.worker_profiles_enabled=True
                status,_,raw=self.request('POST','/v1/messages',full_review_request())
                self.assertEqual(connection.call_args.kwargs['profile'],'client-tools')
            self.assertEqual(status,200)
            self.assertEqual(json.loads(raw)['content'][0]['text'],text)
            self.assertEqual(json.loads(base.Worker.seen[-1]['body'])['max_tokens'],8192)
            self.assert_idle()

    def test_plain_chat_stream_and_completion_share_the_native_lifecycle(self):
        self.manager.web_tools_enabled=True
        for requested in (False,True):
            self.reply([{'type':'text','text':'Useful answer'}],reason='max_tokens')
            status,_,raw=self.request('POST','/v1/chat/completions?trace=one',self.payload(requested))
            self.assertEqual(status,200)
            self.assertEqual(base.Worker.seen[-1]['path'],'/v1/messages?trace=one')
            upstream=json.loads(base.Worker.seen[-1]['body'])
            self.assertNotIn('tools',upstream)
            self.assertNotIn('tool_choice',upstream)
            if requested:
                packets=[line[6:] for line in raw.splitlines() if line.startswith(b'data: ')]
                self.assertEqual(packets[-1],b'[DONE]')
                values=[json.loads(p) for p in packets[:-1]]
                for value in values:
                    self.assertTrue(value['id']);self.assertEqual(value['model'],'fixture')
                    self.assertEqual(value['object'],'chat.completion.chunk')
                    self.assertEqual(value['choices'][0]['index'],0)
                self.assertEqual(values[-1]['choices'][0]['finish_reason'],'length')
                self.assertEqual(''.join(v['choices'][0]['delta'].get('content','') for v in values),'Useful answer')
            else:
                value=json.loads(raw)
                self.assertEqual(value['model'],'fixture')
                self.assertEqual(value['choices'][0]['finish_reason'],'length')
                self.assertEqual(value['choices'][0]['message']['content'],'Useful answer')
            self.assert_idle()

    def test_plain_chat_empty_hidden_thinking_remains_an_error(self):
        self.manager.web_tools_enabled=True
        self.reply([{'type':'thinking','thinking':'Synthetic reasoning'}],reason='max_tokens')
        status,_,raw=self.request('POST','/v1/chat/completions',{**self.payload(True),'thinking':{'type':'disabled'}})
        self.assertEqual(status,502)
        self.assertNotIn(b'[DONE]',raw)
        self.assertEqual(self.manager.get_account(self.identity).get('consecutive_failures',0),0)
        self.assert_idle()

    def test_plain_chat_preserves_real_reasoning_as_an_extension(self):
        self.manager.web_tools_enabled=True
        self.reply([{'type':'thinking','thinking':'Synthetic reasoning'},{'type':'text','text':'Answer'}])
        status,_,raw=self.request('POST','/v1/chat/completions',self.payload())
        self.assertEqual(status,200)
        message=json.loads(raw)['choices'][0]['message']
        self.assertEqual(message['content'],'Answer')
        self.assertEqual(message['reasoning_content'],'Synthetic reasoning')
        self.assert_idle()

    def test_disabled_thinking_renumbers_visible_stream_blocks(self):
        blocks=[{'type':'thinking','thinking':'Synthetic private reasoning'}, {'type':'text','text':'Useful answer'}]
        for requested in (False,True):
            self.reply(blocks)
            status,_,raw=self.request('POST','/v1/messages',{**self.payload(requested),'thinking':{'type':'disabled'}})
            self.assertEqual(status,200)
            self.assertNotIn(b'Synthetic private reasoning',raw)
            if requested:
                values=[json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b'data: ')]
                self.assertEqual([v['index'] for v in values if v['type']=='content_block_start'],[0])
                self.assertEqual(values[-1]['type'],'message_stop')
            else:self.assertEqual(json.loads(raw)['content'],[{'type':'text','text':'Useful answer'}])
            self.assert_idle()

    def test_classifier_no_verdict_is_not_account_failure_and_not_replayed(self):
        self.reply([{'type':'thinking','thinking':'Synthetic reasoning'}],reason='max_tokens')
        for _ in range(3):
            status,headers,raw=self.request('POST','/v1/messages',review())
            self.assertEqual(status,502)
            self.assertEqual(dict(headers).get('x-should-retry'),'false')
            self.assertNotIn(b'Synthetic reasoning',raw)
        self.assertEqual(len(base.Worker.seen),3)
        self.assertEqual(self.manager.get_account(self.identity)['status'],'ready')
        self.assertEqual(self.manager.get_account(self.identity).get('consecutive_failures',0),0)
        self.assert_idle()

    def classifier_pool_failover(self, streaming):
        second=base.ManagerTests.second_account(self)
        first=self.manager.get_account(self.identity)
        base.Worker.replies['Bearer '+first['key']]=(500,b'{"error":{"type":"no_cookie_available","code":500}}')
        verdict='<severity>72</severity><category>Data Exfiltration</category>'
        base.Worker.replies['Bearer '+self.manager.get_account(second)['key']]=(200,stream([{'type':'text','text':verdict}]),'text/event-stream')
        status,_,raw=self.request('POST','/v1/messages',{**review(),'stream':streaming})
        self.assertEqual(status,200)
        if streaming:
            values=[json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b'data: ')]
            actual=''.join(value.get('delta',{}).get('text','') for value in values)
        else:actual=json.loads(raw)['content'][0]['text']
        self.assertEqual(actual,verdict)
        self.assertEqual(len(base.Worker.seen),2)
        self.assertEqual(base.Worker.seen[0]['body'],base.Worker.seen[1]['body'])
        self.assertNotEqual(base.Worker.seen[0]['headers']['Authorization'],base.Worker.seen[1]['headers']['Authorization'])
        self.assertEqual(first.get('consecutive_failures',0),0)
        self.assertGreater(first['cooldown_until'],__import__('time').time())
        self.assert_idle()

    def test_classifier_unavailable_session_switches_before_json_verdict(self):
        self.classifier_pool_failover(False)

    def test_classifier_unavailable_session_switches_before_streamed_verdict(self):
        self.classifier_pool_failover(True)

    def test_classifier_header_timeout_leaves_time_for_account_failover(self):
        import time
        from manager import Handler
        second=base.ManagerTests.second_account(self)
        first=self.manager.get_account(self.identity)
        original_post=base.Worker.do_POST
        original_timeout=Handler.upstream_timeout
        ceilings=[]
        def delayed(worker):
            if worker.headers.get('Authorization')=='Bearer '+first['key']:
                worker.rfile.read(int(worker.headers['Content-Length']))
                time.sleep(.2)
                worker.close_connection=True
                return
            original_post(worker)
        def bounded(handler, connection, ceiling=None):
            ceilings.append(ceiling)
            return original_timeout(handler,connection,ceiling=.05 if ceiling is not None else None)
        self.manager.request_seconds=1
        verdict='<severity>72</severity><category>Data Exfiltration</category>'
        base.Worker.replies['Bearer '+self.manager.get_account(second)['key']]=(200,stream([{'type':'text','text':verdict}]),'text/event-stream')
        with patch.object(base.Worker,'do_POST',delayed),patch.object(Handler,'upstream_timeout',bounded):
            status,_,raw=self.request('POST','/v1/messages',review())
        self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)['content'][0]['text'],verdict)
        self.assertEqual(ceilings.count(18),2)
        self.assertIn(None,ceilings)
        self.assertEqual(first.get('consecutive_failures',0),1)
        self.assertEqual(first['status'],'ready')
        self.assert_idle()

    def test_classifier_header_ceiling_does_not_limit_ordinary_messages_or_body_reads(self):
        import time,types
        from manager import Handler
        handler=object.__new__(Handler)
        handler.request_deadline=time.monotonic()+30
        handler.server=types.SimpleNamespace(manager=types.SimpleNamespace(worker_seconds=60))
        from unittest.mock import Mock
        connection=types.SimpleNamespace(sock=Mock())
        handler.upstream_timeout(connection,ceiling=12)
        self.assertEqual(connection.sock.settimeout.call_args.args[0],12)
        handler.upstream_timeout(connection)
        self.assertGreater(connection.sock.settimeout.call_args.args[0],29)

    def test_invalid_classifier_does_not_try_another_account_for_a_verdict(self):
        second=base.ManagerTests.second_account(self)
        self.reply([{'type':'text','text':'<severity>2'}])
        base.Worker.replies['Bearer '+self.manager.get_account(second)['key']]=(200,stream([{'type':'text','text':'<severity>2</severity>'}]),'text/event-stream')
        status,headers,_=self.request('POST','/v1/messages',review())
        self.assertEqual(status,502)
        self.assertEqual({k.lower():v for k,v in headers.items()}['x-should-retry'],'false')
        self.assertEqual(len(base.Worker.seen),1)
        self.assert_idle()

    def test_classifier_retry_policy_resets_on_a_reused_http_connection(self):
        import http.client
        second=base.ManagerTests.second_account(self)
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        headers={'Authorization':'Bearer api-password','Content-Type':'application/json'}
        try:
            self.reply([{'type':'text','text':'<severity>2'}])
            connection.request('POST','/v1/messages',json.dumps(review()),headers)
            response=connection.getresponse();self.assertEqual(response.status,502);response.read()
            first=self.manager.get_account(self.identity)
            self.manager.last_used[self.identity]=0
            self.manager.last_used[second]=1
            base.Worker.replies['Bearer '+first['key']]=(500,b'{"error":{"type":"no_cookie_available","code":500}}')
            base.Worker.replies['Bearer '+self.manager.get_account(second)['key']]=(200,stream([{'type':'text','text':'<severity>2</severity>'}]),'text/event-stream')
            connection.request('POST','/v1/messages',json.dumps(review()),headers)
            response=connection.getresponse();self.assertEqual(response.status,200)
            self.assertIsNone(response.getheader('x-should-retry'));response.read()
            self.assertEqual(len(base.Worker.seen),3)
        finally:connection.close()
        self.assert_idle()

    def test_hidden_thinking_cannot_complete_as_an_empty_success(self):
        for requested in (False, True):
            self.reply([{'type':'thinking','thinking':'Synthetic reasoning'}],reason='max_tokens')
            status,_,raw=self.request('POST','/v1/messages',{**self.payload(requested),'thinking':{'type':'disabled'}})
            self.assertEqual(status,502)
            self.assertNotIn(b'Synthetic reasoning',raw)
            self.assertEqual(self.manager.get_account(self.identity).get('consecutive_failures',0),0)
            self.assert_idle()

    def test_real_classifier_allow_and_block_are_forwarded_unchanged(self):
        for text in ('<severity>2</severity>','<severity>72</severity><category>Data Exfiltration</category>'):
            self.reply([{'type':'thinking','thinking':'Synthetic reasoning'},{'type':'text','text':text}])
            status,_,raw=self.request('POST','/v1/messages',review())
            self.assertEqual(status,200)
            self.assertEqual(json.loads(raw)['content'],[{'type':'text','text':text}])
            self.assertEqual(json.loads(base.Worker.seen[-1]['body'])['max_tokens'],1024)
            self.assert_idle()

    def test_new_classifier_allow_and_block_are_forwarded_unchanged(self):
        for text in ('<block>no</block>', '<block>yes</block>'):
            self.reply([{'type':'thinking','thinking':'Synthetic reasoning'},{'type':'text','text':text}])
            status,_,raw=self.request('POST','/v1/messages',{**review(),'stop_sequences':['</block>']})
            self.assertEqual(status,200)
            self.assertEqual(json.loads(raw)['content'],[{'type':'text','text':text}])
            self.assertEqual(json.loads(base.Worker.seen[-1]['body'])['max_tokens'],1024)
            self.assert_idle()

    def test_streaming_classifier_validates_before_delivering_a_verdict(self):
        for text in ('<block>no</block>', '<block>yes</block>'):
            self.reply([{'type':'thinking','thinking':'Synthetic reasoning'},{'type':'text','text':text}])
            status,headers,raw=self.request('POST','/v1/messages',
                {**review(),'stop_sequences':['</block>'],'stream':True})
            self.assertEqual(status,200)
            self.assertIn('text/event-stream',{k.lower():v for k,v in headers.items()}['content-type'])
            values=[json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b'data: ')]
            self.assertEqual(''.join(v.get('delta',{}).get('text','') for v in values),text)
            self.assertEqual(values[-1]['type'],'message_stop')
            self.assertNotIn(b'Synthetic reasoning',raw)
            self.assertEqual(json.loads(base.Worker.seen[-1]['body'])['max_tokens'],1024)
        for text in ('<block>no', '<block>maybe</block>'):
            self.reply([{'type':'text','text':text}])
            status,headers,raw=self.request('POST','/v1/messages',
                {**review(),'stop_sequences':['</block>'],'stream':True})
            self.assertEqual(status,502)
            self.assertEqual(dict(headers).get('x-should-retry'),'false')
            self.assertNotIn(b'content_block_delta',raw)
        self.assertEqual(len(base.Worker.seen),4)
        self.assert_idle()

    def test_classifier_stream_state_does_not_leak_to_the_next_request(self):
        import http.client
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        headers={'Authorization':'Bearer api-password','Content-Type':'application/json'}
        try:
            self.reply([{'type':'text','text':'<block>no</block>'}])
            connection.request('POST','/v1/messages',json.dumps(
                {**review(),'stop_sequences':['</block>'],'stream':True}),headers)
            response=connection.getresponse()
            self.assertEqual(response.status,200)
            self.assertIn('text/event-stream',response.getheader('Content-Type'))
            response.read()
            self.reply([{'type':'text','text':'Ordinary answer'}])
            connection.request('POST','/v1/messages',json.dumps(self.payload()),headers)
            response=connection.getresponse()
            self.assertEqual(response.status,200)
            self.assertIn('application/json',response.getheader('Content-Type'))
            self.assertEqual(json.loads(response.read())['content'][0]['text'],'Ordinary answer')
        finally:
            connection.close()
        self.assert_idle()

    def test_profile_uses_the_original_image_proxy_and_existing_account_slot(self):
        self.manager.worker_profiles_enabled=True
        account=self.manager.acquire(self.identity)
        source=(__import__('pathlib').Path(account['directory'])/'clewdr.toml').read_text()
        with patch.object(self.manager,'run_worker',wraps=self.manager.run_worker) as run:
            self.assertEqual(endpoint(self.manager,account,'client-tools'),account['port'])
            self.assertEqual(self.manager.total,1)
            call=run.call_args
            self.assertEqual(call.args[0]['image'],account['image'])
            config=(__import__('pathlib').Path(call.kwargs['directory'])/'clewdr.toml').read_text()
            self.assertIn('web_search = false',config)
            import tomllib
            original,clone=tomllib.loads(source),tomllib.loads(config)
            self.assertIn('Continue the API conversation',clone.pop('custom_prompt'))
            original.pop('custom_prompt',None);clone['web_search']=True
            self.assertEqual(clone,original)
            self.assertEqual((__import__('pathlib').Path(account['directory'])/'clewdr.toml').read_text(),source)
            endpoint(self.manager,account,'client-tools');self.assertEqual(run.call_count,1)
        self.manager.release(account);self.assert_idle()
        self.manager.control(self.identity,'pause')
        self.assertNotIn(account['container']+'-tools',self.manager.containers)

    def test_profile_creation_failure_releases_the_account(self):
        self.manager.worker_profiles_enabled=True;account=self.manager.acquire(self.identity)
        with patch('worker_profiles.endpoint',side_effect=OSError('private-path')):
            with self.assertRaises(base.Problem):self.manager.worker_connection(account,1,profile='client-tools')
        self.assert_idle()

    def test_expired_profile_is_not_stopped_while_leased(self):
        from pathlib import Path
        from worker_profiles import expire, warm
        self.manager.worker_profiles_enabled=True;account=self.manager.acquire(self.identity)
        endpoint(self.manager,account,'client-tools')
        manifest=Path(account['directory'])/'profiles/client-tools/profile.json'
        data=json.loads(manifest.read_text());data['used_at']=0;manifest.write_text(json.dumps(data))
        expire(self.manager)
        self.assertIn(account['container']+'-tools',self.manager.containers)
        self.manager.release(account);expire(self.manager)
        self.assertNotIn(account['container']+'-tools',self.manager.containers)
        self.assertFalse(manifest.exists());self.assertFalse(warm(account))
        manifest.write_text('corrupt-json');self.assertFalse(warm(account))

    def test_new_profiles_evict_idle_profiles_and_keep_busy_accounts(self):
        from pathlib import Path
        self.manager.worker_profiles_enabled=True
        accounts=[self.manager.get_account(self.identity)]
        for n in range(3):
            identity=self.manager.add({'name':'profile-'+str(n),
                'sessionKey':'sk-ant-sid02-'+chr(66+n)*100+'-ABCDEFAA', 'proxy':'127.0.0.1:1080'})
            accounts.append(self.manager.get_account(identity))
        for account in accounts[:2]:endpoint(self.manager,account,'client-tools')
        busy=self.manager.acquire(accounts[0]['id'])
        endpoint(self.manager,accounts[2],'client-tools')
        self.assertIn(busy['container']+'-tools',self.manager.containers)
        self.manager.release(busy)
        endpoint(self.manager,accounts[3],'client-tools')
        remaining=[a for a in accounts if a['container']+'-tools' in self.manager.containers]
        self.assertEqual(len(remaining),2)
        self.assertTrue(all((Path(a['directory'])/'profiles/client-tools/profile.json').exists() for a in remaining))
