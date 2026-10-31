"""Deliberately small MCP confirmation form subset; unknown forms fail closed."""
from iam_service import IAMError


def fields(params):
    if params.get('mode') != 'form' or not isinstance(params.get('message'), str):
        raise IAMError('Unsupported MCP prompt mode')
    schema = params.get('requestedSchema', {})
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        raise IAMError('Unsupported MCP form')
    if set(schema) - {'type', 'properties', 'required', 'additionalProperties', 'title', 'description', '$schema'}:
        raise IAMError('Unsupported MCP schema constraints')
    if schema.get('additionalProperties', False) not in (True, False):
        raise IAMError('Unsupported additional property schema')
    props = schema.get('properties', {})
    required = schema.get('required', [])
    if not isinstance(props, dict) or len(props) > 12 or not isinstance(required, list) or any(k not in props for k in required):
        raise IAMError('Unsupported MCP fields')
    result = []
    for key, spec in props.items():
        if not isinstance(spec, dict) or set(spec) - {'type', 'title', 'description', 'enum', 'enumNames', 'default'}:
            raise IAMError('Unsupported MCP field constraints')
        kind = spec.get('type')
        options = spec.get('enum')
        if kind != 'boolean' and not (kind == 'string' and isinstance(options, list) and 0 < len(options) <= 30 and all(isinstance(v, str) for v in options)):
            raise IAMError('Only boolean and enumerated MCP confirmation fields are supported')
        if kind == 'boolean' and options is not None:
            raise IAMError('Unsupported boolean constraints')
        result.append({'key': key, 'type': kind, 'label': spec.get('title') or key,
                       'description': spec.get('description', ''), 'options': options, 'required': key in required})
    return result


def persistence_options(params):
    meta = params.get('_meta')
    if not isinstance(meta, dict) or meta.get('codex_approval_kind') != 'mcp_tool_call':
        return []
    offered = meta.get('persist', [])
    if isinstance(offered, str):
        offered = [offered]
    if not isinstance(offered, list):
        return []
    return [value for value in ('session', 'always') if value in offered]


def response(params, decision, content, persist=None):
    if persist is not None and (decision != 'accept' or persist not in persistence_options(params)):
        raise IAMError('This persistence choice was not offered for approval')
    if decision == 'decline':
        return {'action': 'decline', 'content': None}
    if decision != 'accept' or not isinstance(content, dict):
        raise IAMError('Explicit form response required')
    specs = fields(params)
    if set(content) - {s['key'] for s in specs}:
        raise IAMError('Unexpected MCP response field')
    for spec in specs:
        if spec['key'] not in content:
            if spec['required']: raise IAMError('Complete all required fields')
            continue
        value = content[spec['key']]
        if spec['type'] == 'boolean':
            if type(value) is not bool: raise IAMError('Choose yes or no explicitly')
        elif value not in spec['options']:
            raise IAMError('Choose an offered option')
    result = {'action': 'accept', 'content': content}
    if persist is not None:
        result['_meta'] = {'persist': persist}
    return result
