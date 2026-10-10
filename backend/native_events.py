"""Normalize real webpage tool events and authenticate source/Thinking history."""
import copy
import hashlib
import json
import re
from history_state import cipher, seal
from message_stream import event
from source_text import text, quote
from web_files import FileProblem

SOURCE_PREFIX='webccsource_v1_'


def source_token(manager,owner,source):
    return SOURCE_PREFIX+cipher(manager,'sources').encrypt(json.dumps({'owner':owner,'source':source},ensure_ascii=False).encode()).decode()


class NativeEvents:
    def __init__(self,manager,owner,thinking=True):
        self.manager,self.owner=manager,owner
        self.buffer,self.model=b'','unknown'
        self.blocks,self.sources,self.words={},{},{}
        self.result=None; self.inputs={}; self.finished=False
        self.thinking, self.hidden, self.indices = thinking, set(), {}

    def visible(self, value):
        kind, index = value.get('type'), value.get('index')
        if kind == 'content_block_start':
            if not self.thinking and value['content_block']['type'] in {'thinking', 'redacted_thinking'}:
                self.hidden.add(index)
            elif index not in self.hidden:
                self.indices[index] = len(self.indices)
        if index in self.hidden:
            return None
        if index is not None:
            if index not in self.indices:
                raise ValueError('Content delta has no visible block start')
            value['index'] = self.indices[index]
        return value

    def frame(self,value):
        value=copy.deepcopy(value);kind=value.get('type');index=value.get('index')
        if kind=='message_start':self.model=value['message'].get('model','unknown')
        if kind=='content_block_start':
            block=value['content_block'];self.blocks[index]=copy.deepcopy(block)
            if block['type']=='tool_use' and block.get('name') in {'web_search','web_fetch'}:
                value['content_block']={k:block[k] for k in ('id','name','input')};value['content_block']['type']='server_tool_use'
            elif block['type']=='tool_result' and block.get('name') in {'web_search','web_fetch'}:
                entries=[s for s in block.get('content',[]) if isinstance(s,dict) and s.get('type')=='knowledge' and isinstance(s.get('url'),str)]
                for s in entries:self.sources[s['url']]=s
                if block['name']=='web_search':
                    content=[{'type':'web_search_result','url':s['url'],'title':s.get('title',''),
                              'encrypted_content':source_token(self.manager,self.owner,s)} for s in entries]
                    value['content_block']={'type':'web_search_tool_result','tool_use_id':block['tool_use_id'],'content':content if not block.get('is_error') else {'type':'web_search_tool_result_error','error_code':'unavailable'}}
                else:
                    content={'type':'web_fetch_tool_result_error','error_code':'unavailable'}
                    if entries and not block.get('is_error'):
                        s=entries[0]
                        try:
                            fetched=text(self.manager,self.owner,s['url'])
                            self.sources[s['url']]={**s,'verified_text':fetched['text']}
                            content={'type':'web_fetch_result','url':s['url'],'retrieved_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                                'content':{'type':'document','title':s.get('title',''),'citations':{'enabled':True},
                                'source':{'type':'text','media_type':'text/plain','data':fetched['text']}}}
                        except FileProblem:
                            pass
                    value['content_block']={'type':'web_fetch_tool_result','tool_use_id':block['tool_use_id'],'content':content}
            elif block['type']=='text':
                value['content_block']={k:v for k,v in block.items() if k in ('type','text','citations')}
        elif kind=='content_block_delta':
            delta=value['delta'];typ=delta.get('type')
            block=self.blocks.get(index,{})
            if typ=='thinking_delta':block['thinking']=block.get('thinking','')+delta['thinking']
            elif typ=='signature_delta':block['signature']=block.get('signature','')+delta['signature']
            elif typ=='text_delta':self.words[index]=self.words.get(index,'')+delta.get('text','')
            elif typ=='citation_start_delta':
                citation=delta.get('citation',{});url=citation.get('url');source=self.sources.get(url)
                if not source:return []
                try:
                    original=source.get('verified_text') or text(self.manager,self.owner,url)['text']
                    cited=quote(original,self.words.get(index,'')[-2048:])
                    if not cited:return []
                    provenance={'url':url,'title':source.get('title',''),'quote':cited,'sha256':hashlib.sha256(original.encode()).hexdigest()}
                    value['delta']={'type':'citations_delta','citation':{'type':'web_search_result_location',
                        'url':url,'title':source.get('title',''),'cited_text':cited,
                        'encrypted_index':source_token(self.manager,self.owner,provenance)}}
                except FileProblem:
                    return []
            elif typ in {'citation_end_delta','tool_use_block_update_delta'}:return []
        elif kind=='content_block_stop':
            block=self.blocks.get(index,{})
            if block.get('type')=='thinking' and not block.get('signature'):
                signed=seal(self.manager,self.owner,block,self.model)
                return [event('content_block_delta',index=index,delta={'type':'signature_delta','signature':signed['signature']}),event(kind,**{k:v for k,v in value.items() if k!='type'})]
        return [event(kind,**{k:v for k,v in value.items() if k!='type'})]

    def feed(self,chunk):
        self.buffer+=chunk;output=[]
        if len(self.buffer)>1048576:raise ValueError('Native event exceeds limit')
        while True:
            match=re.search(b'\r?\n\r?\n',self.buffer)
            if not match:break
            raw,self.buffer=self.buffer[:match.start()],self.buffer[match.end():]
            payload=b'\n'.join(line[5:].lstrip() for line in raw.splitlines() if line.startswith(b'data:'))
            if not payload:
                output.append(raw+b'\n\n');continue
            try:value=json.loads(payload)
            except ValueError:output.append(raw+b'\n\n');continue
            packets = self.frame(value)
            if self.thinking:
                output.extend(packets)
                continue
            for packet in packets:
                payload = next((line[6:] for line in packet.splitlines() if line.startswith(b'data: ')), None)
                visible = self.visible(json.loads(payload)) if payload else None
                if visible is not None:
                    output.append(event(visible.pop('type'), **visible))
        for packet in output:
            payload=next((line[6:] for line in packet.splitlines() if line.startswith(b'data: ')),None)
            if payload:self.collect(json.loads(payload))
        return b''.join(output)

    def collect(self,value):
        kind=value.get('type');index=value.get('index')
        if kind=='message_start':
            self.result=copy.deepcopy(value['message']);self.result['content']=[]
        elif kind=='content_block_start' and self.result is not None:
            self.result['content'].append(copy.deepcopy(value['content_block']))
        elif kind=='content_block_delta' and self.result is not None:
            block=self.result['content'][index];delta=value['delta'];typ=delta.get('type')
            if typ in {'text_delta','thinking_delta','signature_delta'}:
                key={'text_delta':'text','thinking_delta':'thinking','signature_delta':'signature'}[typ]
                block[key]=block.get(key,'')+delta[key]
            elif typ=='input_json_delta':self.inputs[index]=self.inputs.get(index,'')+delta['partial_json']
            elif typ=='citations_delta':block.setdefault('citations',[]).append(delta['citation'])
        elif kind=='content_block_stop' and index in self.inputs:
            self.result['content'][index]['input']=json.loads(self.inputs.pop(index))
        elif kind=='message_delta' and self.result is not None:
            self.result.update(value.get('delta',{}));self.result.setdefault('usage',{}).update(value.get('usage',{}))
        elif kind=='message_stop':self.finished=True


def restore(manager,owner,fields):
    from cryptography.fernet import InvalidToken
    for message in fields.get('messages') or []:
        blocks=message.get('content') if isinstance(message,dict) else None
        if not isinstance(blocks,list):continue
        for block in blocks:
            if not isinstance(block,dict):continue
            if block.get('type') == 'web_search_tool_result':
                for item in block.get('content', []) if isinstance(block.get('content'), list) else []:
                    token = item.get('encrypted_content', '') if isinstance(item, dict) else ''
                    if token.startswith(SOURCE_PREFIX):
                        try:
                            state = json.loads(cipher(manager, 'sources').decrypt(token[len(SOURCE_PREFIX):].encode(), ttl=86400))
                            if state['owner'] != owner or state['source']['url'] != item.get('url'): raise ValueError()
                        except (InvalidToken, ValueError, KeyError, TypeError):
                            raise FileProblem(400, 'WebCC source state is invalid, expired or belongs to another caller') from None
            for citation in block.get('citations',[]) if isinstance(block.get('citations'),list) else []:
                token=citation.get('encrypted_index','') if isinstance(citation,dict) else ''
                if isinstance(token,str) and token.startswith(SOURCE_PREFIX):
                    try:
                        state=json.loads(cipher(manager,'sources').decrypt(token[len(SOURCE_PREFIX):].encode(),ttl=86400))
                        source=state['source']
                        if state['owner']!=owner or source['url']!=citation.get('url') or source.get('quote')!=citation.get('cited_text'):
                            raise ValueError()
                    except (InvalidToken,ValueError,KeyError):
                        raise FileProblem(400,'WebCC source citation is invalid, expired or belongs to another caller') from None

            if block.get('type') in {'web_search_tool_result', 'web_fetch_tool_result'} or block.get('type') == 'server_tool_use' and block.get('name') in {'web_search', 'web_fetch'}:
                historical = json.dumps(block, ensure_ascii=False)
                block.clear()
                block.update(type='text', text='Previous verified web tool context: ' + historical)
