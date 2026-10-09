import copy
import http.client
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from web_files import FileStore, FileProblem, parse_upload
import test_manager as base


def multipart(raw=b'Allocation north=37 south=83', mime='text/plain', filename='report.txt', expiry=None):
    boundary='webcc-fixture-boundary'
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode()+raw+b'\r\n'
    if expiry is not None:
        body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="expires_in_seconds"\r\n\r\n{expiry}\r\n').encode()
    body+=(f'--{boundary}--\r\n').encode()
    return body,'multipart/form-data; boundary='+boundary


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=FileStore(Path(self.temp.name))
    def tearDown(self):self.temp.cleanup()
    def upload(self,owner='A',raw=b'test'):return self.store.put(owner,'test.txt','text/plain',raw)
    def test_persistence_isolation_and_delete(self):
        item=self.upload()
        reopened=FileStore(Path(self.temp.name))
        self.assertEqual(reopened.get('A',item['id'])[6],b'test')
        for operation in [lambda: reopened.get('B',item['id']),lambda: reopened.delete('B',item['id'])]:
            with self.assertRaises(FileProblem) as caught:operation()
            self.assertEqual(caught.exception.status,404)
        self.assertEqual(reopened.list('B','')['data'],[])
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o600)
        self.store.delete('A',item['id'])
        with self.assertRaises(FileProblem):reopened.get('A',item['id'])
    def test_expiration_and_quota(self):
        with patch('web_files.time.time',return_value=100):item=self.store.put('A','a','text/plain',b'test',3600)
        with patch('web_files.time.time',return_value=3701):
            self.assertEqual(self.store.list('A','')['data'],[])
            with self.assertRaises(FileProblem):self.store.get('A',item['id'])
        with patch('web_files.OWNER_QUOTA',3):
            with self.assertRaises(FileProblem):self.upload()
    def test_parallel_quota_is_atomic(self):
        def put(_):
            try:self.upload();return True
            except FileProblem:return False
        with patch('web_files.OWNER_QUOTA',8),ThreadPoolExecutor(4) as pool:
            self.assertEqual(sum(pool.map(put,range(4))),2)
    def test_pagination_and_foreign_cursor(self):
        items=[self.upload() for _ in range(3)]
        first=self.store.list('A','limit=2');self.assertTrue(first['has_more'])
        second=self.store.list('A','limit=2&page='+first['next_page'])
        self.assertEqual(len(second['data']),1)
        self.assertEqual(len({v['id'] for v in first['data']+second['data']}),3)
        self.assertFalse(second['has_more'])
        self.assertEqual(len(self.store.list('A','ids[]='+items[0]['id'])['data']),1)
        with self.assertRaises(FileProblem):self.store.list('B','after_id='+items[0]['id'])
        for query in ['limit=0','limit=2&limit=3','unknown=1','page=x','ids=x&limit=1']:
            with self.assertRaises(FileProblem):self.store.list('A',query)
    def test_resolution_atomic_and_kind_validation(self):
        item=self.upload()
        value={'messages':[{'role':'user','content':[{'type':'document','source':{'type':'file','file_id':item['id']}}]}]}
        original=copy.deepcopy(value);resolved,changed=self.store.resolve('A',value)
        self.assertTrue(changed);self.assertEqual(value,original)
        self.assertEqual(resolved['messages'][0]['content'][0]['source']['data'],'test')
        for owner,kind in [('B','document'),('A','image')]:
            value['messages'][0]['content'][0]['type']=kind
            with self.assertRaises(FileProblem):self.store.resolve(owner,value)
    def test_multipart_validation(self):
        raw,ctype=multipart(filename='../../report.txt',expiry=3600)
        self.assertEqual(parse_upload(ctype,raw),('report.txt','text/plain',b'Allocation north=37 south=83',3600))
        for body,header in [(raw[:-4],ctype),(raw,'application/json'),multipart(mime='application/pdf'),multipart(expiry=2),multipart(expiry='NaN')]:
            with self.assertRaises(FileProblem):parse_upload(header,body) if header=='application/json' or body==raw[:-4] else self.store.put('A',*parse_upload(header,body))


class FileGatewayTests(unittest.TestCase):
    setUp=base.ManagerTests.setUp
    tearDown=base.ManagerTests.tearDown
    request=base.ManagerTests.request
    def raw_request(self,method,path,raw=None,ctype='application/json',key='api-password'):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            c.request(method,path,raw,{'x-api-key':key,'Content-Type':ctype})
            r=c.getresponse();return r.status,dict(r.getheaders()),r.read()
        finally:c.close()
    def test_sdk_shape_lifecycle_and_scopes(self):
        fields={'name':'A','accounts':[self.identity],'scopes':['messages','files'],'rpm':60}
        a=self.manager.api_keys.create(fields);b=self.manager.api_keys.create({**fields,'name':'B'})
        raw,ctype=multipart()
        status,headers,body=self.raw_request('POST','/v1/files?beta=true',raw,ctype,a['key'])
        self.assertEqual(status,200);self.assertEqual(headers['X-WebCC-Adapter'],'managed-files-v1')
        item=json.loads(body);path='/v1/files/'+item['id']
        self.assertEqual(self.raw_request('GET','/v1/files?beta=true&limit=1',key=a['key'])[0],200)
        self.assertEqual(self.raw_request('GET',path+'?beta=true',key=a['key'])[0],200)
        self.assertEqual(self.raw_request('GET',path+'/content',key=a['key'])[2],b'Allocation north=37 south=83')
        self.assertEqual(self.raw_request('GET',path,key=b['key'])[0],404)
        self.assertEqual(self.raw_request('DELETE',path,key=b['key'])[0],404)
        key=self.manager.api_keys.create({**fields,'name':'C','scopes':['messages']})
        self.assertEqual(self.raw_request('GET','/v1/files',key=key['key'])[0],403)
        self.manager.api_keys.revoke(a['id'])
        self.assertEqual(self.raw_request('GET',path,key=a['key'])[0],401)
        self.assertEqual(base.Worker.seen,[])
    def test_file_reference_reaches_worker_and_deleted_fails_before_dispatch(self):
        item=self.manager.files.put('platform','report.txt','text/plain',b'North=37')
        account=self.manager.get_account(self.identity)
        base.Worker.replies['Bearer '+account['key']]=(200,b'{"content":[{"type":"text","text":"37"}]}')
        payload={'model':'claude-sonnet-4-6','max_tokens':128,'messages':[{'role':'user','content':[{'type':'document','source':{'type':'file','file_id':item['id']}}]}]}
        self.assertEqual(self.request('POST','/v1/messages',payload)[0],200)
        self.assertEqual(json.loads(base.Worker.seen[-1]['body'])['messages'][0]['content'][0],{'type':'text','text':'North=37'})
        count=len(base.Worker.seen);self.manager.files.delete('platform',item['id'])
        self.assertEqual(self.request('POST','/v1/messages',payload)[0],404)
        self.assertEqual(len(base.Worker.seen),count)
        self.assertEqual(account['status'],'ready')

    def test_file_admission_released_and_bounded(self):
        self.manager.files.slots.acquire();self.manager.files.slots.acquire()
        try:self.assertEqual(self.raw_request('GET','/v1/files')[0],429)
        finally:self.manager.files.slots.release();self.manager.files.slots.release()
        self.assertEqual(self.raw_request('GET','/v1/files?unknown=1')[0],400)
        self.assertEqual(self.raw_request('GET','/v1/files')[0],200)


if __name__=='__main__':unittest.main()
