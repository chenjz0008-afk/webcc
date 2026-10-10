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
    def test_classifier_recognition_requires_the_complete_protocol(self):
        fields=review();self.assertTrue(is_classifier(fields))
        for changes in ({'max_tokens':2048},{'thinking':{'type':'adaptive'}},{'stop_sequences':['STOP']},
                        {'system':'Classify this'},{'tools':[{'name':'read'}]}):
            self.assertFalse(is_classifier({**fields,**changes}))
        original=copy.deepcopy(fields)
        self.assertEqual(prepare(fields)['max_tokens'],1024)
        self.assertEqual(fields,original)

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
            status,_,raw=self.request('POST','/v1/messages',review())
            self.assertEqual(status,502)
            self.assertNotIn(b'Synthetic reasoning',raw)
        self.assertEqual(len(base.Worker.seen),3)
        self.assertEqual(self.manager.get_account(self.identity)['status'],'ready')
        self.assertEqual(self.manager.get_account(self.identity).get('consecutive_failures',0),0)
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
