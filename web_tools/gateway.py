"""Isolated forwarding policy for the prompt-v1 opt-in adapter."""
import http.client
import json
import queue
import select
import socket
import threading
import time
import uuid
from manager import ClientGone, Problem
from api_keys import KeyProblem
from jsonschema.exceptions import ValidationError, SchemaError, RefResolutionError
from web_tools.api import complete, events, prepare, OutputProblem
from web_files import FileProblem
from runtime_limits import MEDIA_INFLIGHT, TOOL_REQUEST_MAX


MEDIA_SLOTS = threading.BoundedSemaphore(MEDIA_INFLIGHT)


def forward(handler, raw=None, standard=False):
    admitted = False

    def admit():
        nonlocal admitted
        if not admitted:
            if not MEDIA_SLOTS.acquire(blocking=False):
                raise FileProblem(429, 'Media tool requests are busy; retry later')
            admitted = True

    try:
        _forward(handler, admit, raw, standard)
    finally:
        if admitted:
            MEDIA_SLOTS.release()


def _forward(handler, admit, raw=None, standard=False):
    handler.adapter = ('standard-tools-v1; incremental' if standard else 'prompt-v1; buffered') + '; schema-validated; no-native-strict'
    handler.request_id = uuid.uuid4().hex
    handler.request_deadline = time.monotonic() + handler.manager.request_seconds
    if getattr(handler, 'outer_deadline', None):
        handler.request_deadline = min(handler.request_deadline, handler.outer_deadline)
    sent = False

    def alive():
        handler.check_caller()
        if select.select([handler.connection], [], [], 0)[0]:
            if not handler.connection.recv(1, socket.MSG_PEEK):
                raise ClientGone()

    def chunk(data):
        handler.write_stream(('%x\r\n' % len(data)).encode() + data + b'\r\n')

    def error(status, kind, message, code=None):
        detail = {'type': kind, 'message': message}
        if code:
            detail['code'] = code
        if sent:
            chunk(('event: error\ndata: ' + json.dumps({'type': 'error', 'error': detail}) + '\n\n').encode())
            handler.write_stream(b'0\r\n\r\n')
        else:
            handler.respond(status, {'type': 'error', 'error': detail, 'request_id': handler.request_id})

    try:
        from urllib.parse import urlsplit
        if (not standard and handler.headers.get('anthropic-beta')) or urlsplit(handler.path).path != '/v1/messages' or not standard and handler.path != '/v1/messages':
            raise ValueError('Beta and query parameters are unsupported in prompt-v1')
        if int(handler.headers.get('Content-Length', '0')) > 131072:
            admit()
        request, body = prepare(raw if raw is not None else handler.body(TOOL_REQUEST_MAX), handler.manager.files,
                                handler.caller_key or 'platform', on_media=admit, standard=standard, cache=handler.manager.processing_cache)
        if standard and any(t.get('strict') and not t.get('eager_input_streaming') for t in request.get('tools', [])):
            handler.adapter += '; strict-call-validated'
        from history_state import verify
        verify(handler.manager, handler.caller_key or 'platform', request)
    except FileProblem as problem:
        handler.retry_after = problem.retry_after
        handler.close_connection = True
        error(problem.status, 'invalid_request_error', str(problem))
        return
    except (ValueError, TypeError, KeyError, RecursionError, ValidationError, SchemaError, RefResolutionError):
        handler.close_connection = True
        error(400, 'invalid_request_error', 'Invalid tool request; check tool schemas, choice and paired history')
        return
    except Problem as problem:
        handler.close_connection = True
        error(problem.status, 'invalid_request_error', str(problem))
        return
    attempted, last = set(), (503, 'overloaded_error', 'No eligible account is available')
    def waiting():
        if not attempted:
            handler.check_caller()
        alive()
    for _ in range(handler.manager.retry_attempts):
        alive()
        try:
            account = handler.manager.acquire(exclude=attempted, request_deadline=handler.request_deadline,
                                               on_wait=waiting, allowed=handler.allowed_accounts)
        except Problem as problem:
            if not attempted:
                last = (problem.status, 'rate_limit_error' if problem.status == 429 else 'api_error',
                        'Account queue wait limit reached; retry later' if problem.status == 429 else 'No eligible account is available')
            break
        attempted.add(account['id'])
        connection = handler.manager.worker_connection(account, min(handler.manager.worker_seconds, max(.1, handler.request_deadline - time.monotonic())))
        done, cancelled = threading.Event(), threading.Event()
        result, transport = {}, {}
        pending = queue.Queue(maxsize=128)
        incremental = None
        from web_tools.api import output_schema
        if standard and request.get('stream') and not request.get('_citation_sources') and output_schema(request) is None:
            from web_tools.streaming import ToolStream
            incremental = ToolStream(handler.manager, handler.caller_key or 'platform', request, chunk)

        def progress(value):
            while not cancelled.is_set():
                try:
                    pending.put(value, timeout=.2)
                    return
                except queue.Full:
                    pass
            raise ClientGone()

        def fetch():
            try:
                if cancelled.is_set():
                    return
                from node_transport import semantic_headers
                headers = semantic_headers(handler.headers) if standard else {}
                connection.request('POST', handler.path if standard else '/v1/messages', body, {'content-type': 'application/json',
                    'anthropic-version': '2023-06-01', **headers, 'Authorization': 'Bearer ' + account['key'], 'Accept-Encoding': 'identity'})
                transport['socket'] = connection.sock
                if cancelled.is_set():
                    return
                response = connection.getresponse()
                result.update(status=response.status, retry_after=response.getheader('Retry-After'),
                              node_error=response.getheader('X-WebCC-Node-Error') == '1')
                from upstream_message import read
                raw = response.read(1048576) if response.status >= 400 else read(response, thinking=standard, on_event=progress if incremental else None)
                result['body'] = raw
            except Exception as exc:
                result['failure'] = type(exc).__name__
            finally:
                connection.close()
                handler.manager.release(account)
                done.set()

        thread = threading.Thread(target=fetch, daemon=True)
        try:
            thread.start()
        except RuntimeError:
            handler.manager.release(account)
            error(503, 'overloaded_error', 'Experimental worker unavailable')
            return
        heartbeat = time.monotonic()
        try:
            if request.get('stream') and not sent:
                handler.connection.settimeout(2)
                handler.send_response(200)
                for key, value in [('Content-Type', 'text/event-stream'), ('Transfer-Encoding', 'chunked'),
                    ('Cache-Control', 'no-store'), ('X-Accel-Buffering', 'no'),
                    ('X-Request-Id', handler.request_id), ('Request-Id', handler.request_id),
                    ('X-WebCC-Adapter', handler.adapter)]:
                    handler.send_header(key, value)
                handler.end_headers()
                sent = True
            while not done.is_set() or not pending.empty():
                try:
                    value = pending.get(timeout=.2)
                    incremental.upstream(value)
                except queue.Empty:
                    pass
                alive()
                if time.monotonic() >= handler.request_deadline:
                    raise TimeoutError()
                if sent and time.monotonic() - heartbeat >= 5:
                    chunk(b'event: ping\ndata: {"type":"ping"}\n\n')
                    heartbeat = time.monotonic()
        except (ClientGone, OSError, KeyProblem, ValueError) as exc:
            cancelled.set()
            if transport.get('socket'):
                try:
                    transport['socket'].shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            done.wait(1)
            if isinstance(exc, KeyProblem):
                error(exc.status, 'permission_error' if exc.status == 403 else 'authentication_error', 'Caller permission was revoked')
            elif isinstance(exc, TimeoutError):
                error(504, 'api_error', 'Experimental request deadline exceeded', 'request_timeout')
            elif isinstance(exc, ValueError):
                error(502, 'api_error', 'Incremental output failed validation; request was not replayed', 'output_validation_failed')
            else:
                handler.close_connection = True
            return
        status = result.get('status', 502)
        if result.get('node_error'):
            last = (503, 'api_error', 'Worker node unavailable')
            continue
        if result.get('failure'):
            if result['failure'] == 'UnexpectedTool':
                last = (502, 'api_error', 'Unexpected server tool execution; request was not replayed', 'unexpected_server_tool')
                break
            status = 504 if result['failure'] == 'TimeoutError' else 502
            last = (status, 'api_error', 'Experimental upstream connection failed')
        elif status >= 400:
            last = (status, 'rate_limit_error' if status == 429 else 'api_error', 'Experimental upstream rejected request')
        else:
            try:
                message = complete(result['body'], request, handler.manager.processing_cache, handler.caller_key or 'platform')
                from history_state import message as authenticate_history
                message = authenticate_history(handler.manager, handler.caller_key or 'platform', message)
            except OutputProblem as problem:
                print(json.dumps({'request_id': handler.request_id, 'error': problem.code,
                                  'stage': problem.stage, 'stream_started': bool(incremental and incremental.emitted)}), flush=True)
                last = (502, 'api_error', str(problem), problem.code)
                if not problem.retry or incremental and incremental.emitted:
                    break
                upstream = json.loads(body)
                try:
                    previous = json.loads(result['body'])
                except ValueError:
                    previous = {}
                text = ''.join(b.get('text', '') for b in previous.get('content', []) if isinstance(b, dict) and b.get('type') == 'text')
                if len(text.encode()) <= 32768:
                    upstream['messages'].extend([{'role': 'assistant', 'content': text},
                        {'role': 'user', 'content': 'The previous reply failed tool-choice or JSON Schema validation. Correct it using the declared schemas and original task. Return only the required calls/text JSON object. No tool has executed.'}])
                    body = json.dumps(upstream, ensure_ascii=False).encode()
                continue
            alive()
            if sent:
                if incremental and incremental.emitted:
                    try:
                        incremental.finish(message)
                    except ValueError:
                        error(502, 'api_error', 'Incremental output did not complete; request was not replayed', 'output_validation_failed')
                        return
                else:
                    for event in events(message):
                        chunk(event)
                handler.write_stream(b'0\r\n\r\n')
            else:
                handler.respond(200, message)
            handler.manager.record_success(account)
            return
        if status in (401, 403, 429) or status >= 500:
            category = 'transport:' + result['failure'] if result.get('failure') else 'upstream_http:' + str(status)
            handler.manager.record_failure(account, category, status, result.get('retry_after'))
        if status == 400:
            break
    error(*last)
