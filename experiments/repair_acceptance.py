"""Server-only SDK, business and interleaved load acceptance for the candidate."""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request
import anthropic
import openai
from dotenv import dotenv_values

APP = Path('/var/lib/webcc-cluster/repair-candidate/app')
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'experiments'))
import foreign_trade_load as trade
from cluster_state import ClusterState

ROOT = Path(os.environ.get('WEBCC_REPAIR_RESULTS', '/var/lib/webcc-cluster/repair-results'))
CONFIG = json.loads(Path(os.environ.get('WEBCC_REPAIR_CONFIG', '/var/lib/webcc-cluster/repair-candidate/private.json')).read_text())
for path in ('/etc/clewdr-manager.env', '/var/lib/webcc-cluster/runtime-private.env'):
    os.environ.update({k: v for k, v in dotenv_values(path).items() if v is not None})
BASE = CONFIG['base_url']
trade.BASE = BASE
original_workspace = trade.Workspace
original_prompts = dict(trade.PROMPTS)


class Workspace(original_workspace):
    def execute(self, name, arguments):
        if name == 'list_documents':
            value = {'documents': [{'id': k, 'version': v['version']} for k, v in self.docs.items() if k != 'policy']}
            self.log.append({'tool': name, 'input': arguments, 'output': value, 'error': False, 'seconds': 0})
            return value, False
        return super().execute(name, arguments)

    def evaluate(self, role, answer):
        checks = super().evaluate(role, answer)
        target = dict(sales='quote', social='campaign', ads='ad_report', support='reply',
                      procurement='procurement', inventory='inventory', manager='summary', failure='conflict')[role]
        checks['other_documents_unchanged'] = all(self.docs.get(k) == v for k, v in self.initial.items() if k != target)
        checks['only_authorized_writes'] = all(x['input']['id'] == target for x in self.log
                                               if x['tool'] == 'write_document' and not x['error'])
        text = self.docs.get({'social': 'campaign', 'manager': 'summary', 'sales': 'quote'}.get(role, ''), {}).get('content', '').lower()
        if role in {'social', 'manager', 'sales'}:
            checks['no_invented_performance'] = not any(w in text for w in ('保温瓶', 'insulated', 'keeps drinks cold', 'drop-proof', 'dent-proof'))
        if role == 'social':
            checks['complete_ctas'] = sum(text.count(w) for w in ('dm us', 'contact us', 'request a quote', 'message us')) >= 3
        return checks


trade.Workspace = Workspace
trade.TOOLS.append({'name': 'list_documents', 'description': 'List actual document IDs and versions in this workspace. Workspace labels are not document IDs.',
                    'strict': True, 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}})
trade.PROMPTS['social'] = original_prompts['social'].replace('（开头钩子、产品展示、面向批发商的 CTA）', '（每条都有开头钩子、产品展示和独立询盘 CTA，三条各有一句 DM us 或 Request a quote）')
for role in ('sales', 'ads', 'support', 'procurement'):
    trade.PROMPTS[role] += ' 目标文档不存在时，已授权按其明确ID和版本0创建草稿，无需再次确认。'
trade.PROMPTS['manager'] += ' 产品资料没有保温或抗跌落依据，不要加入这些性能。'
for role in trade.PROMPTS:
    trade.PROMPTS[role] += ' 工作区标识不是文件ID；文件ID就是上述名称。必要时用list_documents发现资源。只依据已读取产品事实。'


def call(method, path, body=None):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers={'Authorization': 'Bearer ' + CONFIG['admin'], 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def chat_roundtrip(key, streaming):
    client = openai.OpenAI(api_key=key, base_url=BASE + '/v1', timeout=150, max_retries=0)
    started = time.monotonic()
    timing, documents = [], {'B500': {'version': 1, 'price': 3.20, 'moq': 1000}}
    tools = [{'type': 'function', 'function': {'name': 'read_product', 'strict': True, 'description': 'Read the product by exact SKU.',
              'parameters': {'type': 'object', 'properties': {'sku': {'enum': ['B500']}}, 'required': ['sku'], 'additionalProperties': False}}}]
    messages = [{'role': 'user', 'content': '调用read_product读取B500，再用一句英文告诉英国批发商单价和MOQ。不发送邮件。'}]
    try:
        for turn in range(3):
            begin = time.monotonic(); first = None
            response = client.chat.completions.create(model='claude-sonnet-4-6', messages=messages,
                **({'tools': tools, 'tool_choice': 'required'} if turn == 0 else {}), max_tokens=512, stream=streaming)
            if streaming:
                content, calls, reason = '', {}, None
                for chunk in response:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]; delta = choice.delta
                    if first is None and (delta.content or delta.tool_calls): first = round(time.monotonic()-begin, 3)
                    content += delta.content or ''
                    for item in delta.tool_calls or []:
                        block = calls.setdefault(item.index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                        block['id'] += item.id or ''
                        if item.function:
                            block['function']['name'] += item.function.name or ''
                            block['function']['arguments'] += item.function.arguments or ''
                    if choice.finish_reason: reason = choice.finish_reason
                message = {'role': 'assistant', 'content': content or None, **({'tool_calls': list(calls.values())} if calls else {})}
            else:
                choice = response.choices[0]; reason = choice.finish_reason
                message = choice.message.model_dump(exclude_none=True)
            timing.append({'turn': turn, 'seconds': round(time.monotonic()-begin, 3), 'first_content': first, 'finish_reason': reason})
            messages.append(message)
            calls = message.get('tool_calls', [])
            if not calls:
                answer = message.get('content') or ''
                assert '1000' in answer.replace(',', '') and '3.2' in answer
                return {'pass': True, 'stream': streaming, 'requests': timing, 'seconds': round(time.monotonic()-started, 3), 'answer': answer}
            for tool in calls:
                arguments = json.loads(tool['function']['arguments'])
                assert tool['function']['name'] == 'read_product' and arguments == {'sku': 'B500'}
                messages.append({'role': 'tool', 'tool_call_id': tool['id'], 'content': json.dumps(documents['B500'])})
        raise RuntimeError('Chat tool loop did not finish')
    except Exception as error:
        return {'pass': False, 'stream': streaming, 'requests': timing, 'seconds': round(time.monotonic()-started, 3), 'error': str(error)[:600]}
    finally:
        client.close()


def supplements(key, foreign_key):
    client = anthropic.Anthropic(api_key=key, base_url=BASE, max_retries=0, timeout=180)
    foreign = anthropic.Anthropic(api_key=foreign_key, base_url=BASE, max_retries=0, timeout=30)
    checks, ids = {}, []
    csv = b'channel,spend,revenue,leads\nTikTok,1200,7200,60\nLinkedIn,1500,4500,30\n'
    try:
        begin = time.monotonic()
        try:
            file = client.beta.files.upload(file=('metrics.csv', csv, 'text/csv')); ids.append(file.id)
            try:
                foreign.beta.files.download(file.id)
                raise AssertionError('Foreign caller obtained file')
            except anthropic.NotFoundError:
                pass
            message = client.beta.messages.create(model='claude-sonnet-4-6', max_tokens=4096,
                tools=[{'name': 'code_execution', 'type': 'code_execution_20260521'}], messages=[{'role': 'user', 'content': [
                    {'type': 'container_upload', 'file_id': file.id}, {'type': 'text', 'text':
                     '真实运行Python读取input/metrics.csv，用pandas计算ROAS=revenue/spend和CPL=spend/leads，生成output/analysis.csv，列channel,roas,cpl。返回生成文件，不要仅给代码。'}]}])
            generated = [f['file_id'] for b in message.content if b.type == 'code_execution_tool_result' for f in b.model_dump()['content']['content']]
            assert generated; ids.extend(generated)
            raw = client.beta.files.download(generated[0]).read().decode()
            import csv as parser, io
            rows = {r['channel']: (float(r['roas']), float(r['cpl'])) for r in parser.DictReader(io.StringIO(raw))}
            assert rows == {'TikTok': (6, 20), 'LinkedIn': (3, 50)}
            checks['csv_pandas'] = {'pass': True, 'seconds': round(time.monotonic()-begin, 3), 'csv': raw}
        except Exception as error:
            checks['csv_pandas'] = {'pass': False, 'seconds': round(time.monotonic()-begin, 3), 'error': str(error)[:600]}
        begin = time.monotonic()
        try:
            from source_text import fetch
            source = fetch('https://support.google.com/youtube/answer/10059070?hl=en')
            text = source['text']; assert len(text) > 100 and 'Shorts' in text
            answer = client.messages.create(model='claude-sonnet-4-6', max_tokens=8192, messages=[{'role': 'user', 'content': [
                {'type': 'document', 'title': 'YouTube official Shorts help', 'source': {'type': 'text', 'data': text}, 'citations': {'enabled': True}},
                {'type': 'text', 'text': '依据这份官方说明，为中国外贸企业解释Shorts视频长度要求并给一条产品展示建议，最多两处短引用，全文不超过200字。不要编造产品认证。'}]}])
            citations = [c.model_dump() for b in answer.content if b.type == 'text' for c in b.citations or []]
            assert citations and all(c['cited_text'] in text for c in citations)
            checks['official_source_citations'] = {'pass': True, 'seconds': round(time.monotonic()-begin, 3), 'citations': citations,
                'answer': ''.join(b.text for b in answer.content if b.type == 'text')}
        except Exception as error:
            checks['official_source_citations'] = {'pass': False, 'seconds': round(time.monotonic()-begin, 3), 'error': str(error)[:600]}
        return checks
    finally:
        for identity in ids:
            client.beta.files.delete(identity)
        client.close(); foreign.close()


def main():
    os.umask(0o077); ROOT.mkdir(parents=True, exist_ok=False)
    cluster = ClusterState(CONFIG['database_url']); keys = []
    report = {'base_url': BASE, 'actual_workbuddy_tested': False, 'rounds': [], 'cases': [], 'supplements': {}}
    samples, stopped = [], threading.Event()
    def monitor():
        while not stopped.wait(.25):
            try:
                with cluster.pool.connection() as db: occupied = db.execute('SELECT count(*) FROM webcc_occupancy').fetchone()[0]
                samples.append({'occupancy': occupied, 'load1': os.getloadavg()[0], 'available_kib': next(int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))})
            except Exception: pass
    thread = threading.Thread(target=monitor, daemon=True); thread.start()
    try:
        for role in trade.ROLES:
            keys.append(call('POST', '/admin/api-keys', {'name': 'repair trade ' + role, 'accounts': CONFIG['accounts'], 'scopes': ['messages', 'files', 'runs'], 'rpm': 1000}))
        report['chat'] = [chat_roundtrip(keys[0]['key'], stream) for stream in (False, True)]
        print(json.dumps({'chat': report['chat']}, ensure_ascii=False), flush=True)
        report['supplements'] = supplements(keys[0]['key'], keys[1]['key'])
        print(json.dumps({'supplements': report['supplements']}, ensure_ascii=False), flush=True)
        if not all(c['pass'] for c in report['chat']) or not all(c['pass'] for c in report['supplements'].values()):
            raise RuntimeError('Component acceptance failed; load matrix was not started')
        orders = [(1, 2, 4, 8), (4, 1, 8, 2), (2, 8, 1, 4)]
        for repeat, order in enumerate(orders):
            for concurrency in order:
                trade.ROOT = ROOT / f'round-{repeat}-{concurrency}'; trade.ROOT.mkdir()
                begin = time.monotonic()
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                    cases = [f.result() for f in [pool.submit(trade.run_case, concurrency, role, index, keys[index]['key']) for index, role in enumerate(trade.ROLES)]]
                requests = [r for c in cases for r in c['requests']]
                record = {'repeat': repeat, 'concurrency': concurrency, 'completed': sum(c['pass'] for c in cases), 'tasks': len(cases),
                    'request_p50': trade.percentile([r['seconds'] for r in requests], .5), 'request_p95': trade.percentile([r['seconds'] for r in requests], .95),
                    'first_content_p50': trade.percentile([r['first_content'] for r in requests if r['first_content'] is not None], .5),
                    'first_content_p95': trade.percentile([r['first_content'] for r in requests if r['first_content'] is not None], .95),
                    'first_text_p50': trade.percentile([r['first_text'] for r in requests if r['first_text'] is not None], .5),
                    'first_text_p95': trade.percentile([r['first_text'] for r in requests if r['first_text'] is not None], .95),
                    'task_p50': trade.percentile([c['seconds'] for c in cases], .5), 'task_p95': trade.percentile([c['seconds'] for c in cases], .95),
                    'wall_seconds': round(time.monotonic()-begin, 3)}
                report['rounds'].append(record); report['cases'].extend(cases)
                (ROOT/'progress.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)); print(json.dumps(record), flush=True)
    finally:
        stopped.set(); thread.join(5)
        for key in keys:
            call('POST', '/admin/api-keys/' + key['id'] + '/revoke', {})
            with cluster.pool.connection() as db: db.execute("DELETE FROM webcc_entities WHERE kind='api_keys' AND id=%s", (key['id'],))
        with cluster.pool.connection() as db:
            report['cleanup'] = {'occupancy': db.execute('SELECT count(*) FROM webcc_occupancy').fetchone()[0],
                'temporary_keys': db.execute("SELECT count(*) FROM webcc_entities WHERE kind='api_keys' AND body->>'name' LIKE 'repair trade %'").fetchone()[0],
                'active_tasks': db.execute("SELECT count(*) FROM webcc_tasks WHERE state IN ('queued','processing','waiting','canceling') OR body->>'cleanup_pending'='true'").fetchone()[0]}
        report['monitor'] = {'samples': len(samples), 'peak_occupancy': max((s['occupancy'] for s in samples), default=0),
            'max_load1': max((s['load1'] for s in samples), default=0), 'min_available_mib': round(min((s['available_kib'] for s in samples), default=0)/1024, 1)}
        report['completed'] = sum(c['pass'] for c in report['cases']); report['tasks'] = len(report['cases'])
        (ROOT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)); cluster.pool.close()
        print(json.dumps({'completed': report['completed'], 'tasks': report['tasks'], 'cleanup': report['cleanup'], 'monitor': report['monitor']}), flush=True)


if __name__ == '__main__':
    main()
