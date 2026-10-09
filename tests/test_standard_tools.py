import io,json,unittest
from web_tools.api import prepare,complete
from upstream_message import read
from message_stream import events


class StandardToolsTests(unittest.TestCase):
    def fields(self):
        return {'model':'claude-sonnet-4-6','max_tokens':1024,'thinking':{'type':'adaptive'},'cache_control':{'type':'ephemeral'},
            'metadata':{'user_id':'client'},'tools':[{'name':'add','strict':True,'input_schema':{'type':'object','properties':{'a':{'type':'integer'}},'required':['a'],'additionalProperties':False}}],
            'messages':[{'role':'user','content':'Add 37'}]}

    def response(self,reason='end_turn'):
        payload={'model':'actual-model','content':[{'type':'thinking','thinking':'A brief summary','signature':'real-upstream-fixture'},
            {'type':'text','text':'{"calls":[{"name":"add","input":{"a":37}}],"text":""}'}], 'stop_reason':reason,'usage':{'input_tokens':1,'output_tokens':2}}
        response=io.BytesIO(json.dumps(payload).encode());response.length=None;response.getheader=lambda *args:'application/json'
        return response

    def test_controls_preserved_and_real_thinking_returned_with_tool(self):
        fields=self.fields();request,body=prepare(json.dumps(fields),standard=True)
        upstream=json.loads(body)
        for name in ('thinking','cache_control','metadata'):self.assertEqual(upstream[name],fields[name])
        result=complete(read(self.response(),thinking=True),request)
        self.assertEqual(result['model'],'actual-model')
        self.assertEqual(result['content'][0]['signature'],'real-upstream-fixture')
        self.assertEqual(result['content'][1]['name'],'add')
        self.assertIn(b'signature_delta',b''.join(events(result)))
        request['messages'].extend([{'role':'assistant','content':result['content']},{'role':'user','content':[{'type':'tool_result','tool_use_id':result['content'][1]['id'],'content':'120'}]}])
        request.pop('_standard_tools')
        prepare(json.dumps(request),standard=True)

    def test_native_effort_and_cache_marked_blocks(self):
        fields=self.fields()
        fields['output_config']={'effort':'medium'}
        fields['tools'][0].update(type='custom',cache_control={'type':'ephemeral'})
        fields['messages'][0]['content']=[{'type':'text','text':'Add 37','cache_control':{'type':'ephemeral'}}]
        _,body=prepare(json.dumps(fields),standard=True)
        self.assertEqual(json.loads(body)['output_config'],{'effort':'medium'})
        self.assertIn('cache_control',json.loads(body)['messages'][0]['content'])

    def test_standard_refusal_and_truncation_preserved_without_replay(self):
        request,_=prepare(json.dumps(self.fields()),standard=True)
        for reason in ('max_tokens','refusal'):
            self.assertEqual(complete(read(self.response(reason),thinking=True),request)['stop_reason'],reason)
