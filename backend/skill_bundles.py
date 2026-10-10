"""Agent Skills text bundles; scripts execute only in the sandbox runtime."""
import copy
import re
from pathlib import PurePosixPath
import yaml
from web_files import FileProblem


def safe_path(path):
    if not isinstance(path, str) or not path or len(path) > 180 or '\\' in path or '\x00' in path:
        raise FileProblem(400, 'Invalid resource path')
    parts = PurePosixPath(path).parts
    if path.startswith('/') or any(p in {'.', '..'} or not re.fullmatch(r'[A-Za-z0-9_.-]+', p) for p in parts):
        raise FileProblem(400, 'Resource path must be relative and contain safe segments')
    if str(PurePosixPath(path)) != path:
        raise FileProblem(400, 'Resource path must be normalized')
    return path


def validate_bundle(fields):
    if not isinstance(fields, dict) or set(fields) != {'files'}:
        raise FileProblem(400, 'Skill upload requires a files object')
    files = fields['files']
    if not isinstance(files, dict) or not 1 <= len(files) <= 64 or 'SKILL.md' not in files:
        raise FileProblem(400, 'Provide SKILL.md and at most 64 UTF-8 text resources')
    for path, text in files.items():
        safe_path(path)
        if not isinstance(text, str) or '\x00' in text or len(text.encode()) > 65536:
            raise FileProblem(400, 'Skill resources must be UTF-8 text up to 64 KiB each')
    if sum(len(t.encode()) for t in files.values()) > 262144:
        raise FileProblem(413, 'Skill bundle exceeds 256 KiB')
    text = files['SKILL.md']
    match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)(.*)\Z', text, re.S)
    if not match:
        raise FileProblem(400, 'SKILL.md requires YAML frontmatter')
    try:
        if any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(match[1])):
            raise FileProblem(400, 'YAML aliases are unsupported')
        metadata = yaml.safe_load(match[1])
    except yaml.YAMLError:
        raise FileProblem(400, 'Invalid SKILL.md metadata') from None
    if not isinstance(metadata, dict):
        raise FileProblem(400, 'Skill metadata must be an object')
    name, description = metadata.get('name'), metadata.get('description')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name) or len(name) > 64:
        raise FileProblem(400, 'Skill name must use lowercase letters, digits and hyphens')
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise FileProblem(400, 'Skill description requires 1 to 1024 characters')
    if not match[2].strip():
        raise FileProblem(400, 'Skill instructions cannot be empty')
    return {'name': name, 'description': description, 'instructions': match[2].strip(), 'files': copy.deepcopy(files)}


def snapshot(store, owner, references):
    if not isinstance(references, list) or len(references) > 8:
        raise FileProblem(400, 'Use at most eight explicit skill versions')
    result, names = [], set()
    for ref in references:
        if not isinstance(ref, dict) or set(ref) != {'skill_id', 'version'} or not all(isinstance(v, str) for v in ref.values()):
            raise FileProblem(400, 'Skill references require skill_id and version')
        if ref['version'] == 'latest':
            versions = store.skills(owner, ref['skill_id'])
            if not versions:
                raise FileProblem(404, 'Skill version not found')
            ref = {**ref, 'version': versions[0]['version']}
        bundle = store.skill_version(owner, ref['skill_id'], ref['version'])
        if bundle['name'] in names:
            raise FileProblem(400, 'Duplicate skill name')
        names.add(bundle['name'])
        result.append({**ref, **bundle})
    return result
