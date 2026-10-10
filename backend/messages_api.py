"""Route standard Messages declarations to the existing execution adapters."""
from web_files import FileProblem


def require(handler, *scopes):
    if handler.caller_key:
        granted = set(handler.manager.api_keys.active(handler.caller_key)['scopes'])
        if not set(scopes) <= granted:
            raise FileProblem(403, 'Key is missing a required tool execution permission')


def dispatch(handler, fields):
    tools = fields.get('tools', [])
    if not isinstance(tools, list):
        return False
    container = fields.get('container')
    if isinstance(container, dict):
        container = container.get('id')
    if isinstance(container, str) and handler.manager.tasks:
        item = handler.manager.tasks.get(container, handler.caller_key or 'platform')
        if item['kind'] == 'mcp':
            require(handler, 'mcp')
            from mcp_tasks import messages
            messages(handler, {**fields, 'container': container}, standard=True)
            return True
        if item['kind'] == 'run':
            require(handler, 'runs', 'files')
            from runtime_api import messages
            messages(handler, fields={**fields, 'container': container}, standard=True)
            return True
    if fields.get('mcp_servers'):
        require(handler, 'mcp')
        from mcp_tasks import messages
        messages(handler, fields, standard=True)
        return True
    if any(isinstance(t, dict) and str(t.get('type', '')).startswith('code_execution_') for t in tools):
        if isinstance(fields.get('tool_choice'), dict) and fields['tool_choice'].get('type') == 'none':
            from web_tools.gateway import forward
            import json
            forward(handler, raw=json.dumps({**fields, 'tools': [t for t in tools if isinstance(t, dict) and 'input_schema' in t]}).encode(), standard=True)
            return True
        require(handler, 'runs', 'files')
        if isinstance(fields.get('container'), dict) and fields['container'].get('skills'):
            require(handler, 'skills')
        from runtime_api import messages
        messages(handler, fields=fields, standard=True)
        return True
    history = any(isinstance(b, dict) and b.get('type') in {'tool_use', 'tool_result'}
                  for m in (fields.get('messages') or []) if isinstance(m, dict)
                  for b in (m['content'] if isinstance(m.get('content'), list) else []))
    if history or any(isinstance(m, dict) and m.get('role') == 'system' for m in fields.get('messages', [])) or any(isinstance(t, dict) and 'input_schema' in t for t in tools) or isinstance(fields.get('output_config'), dict) and fields['output_config'].get('format'):
        from web_tools.gateway import forward
        import json
        forward(handler, raw=json.dumps(fields).encode(), standard=True)
        return True
    if any(isinstance(b, dict) and b.get('type') == 'document' and isinstance(b.get('citations'), dict) and b['citations'].get('enabled')
           for m in (fields.get('messages') or []) if isinstance(m, dict)
           for b in (m['content'] if isinstance(m.get('content'), list) else [])):
        from document_citations import messages
        messages(handler, fields=fields, standard=True)
        return True
    return False
