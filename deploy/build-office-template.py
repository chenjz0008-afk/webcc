"""Build once on the server; runtime sandboxes remain offline."""
import json
import os
from pathlib import Path
import sys
from dotenv import dotenv_values
from e2b import Template

for path in ('/etc/clewdr-manager.env', '/var/lib/webcc-cluster/runtime-private.env'):
    os.environ.update({k: v for k, v in dotenv_values(path).items() if v is not None})
sys.path.insert(0, '/opt/clewdr-manager')
from e2b_runtime import E2BRuntime

PACKAGES = ['numpy==2.3.3', 'pandas==2.3.3', 'openpyxl==3.1.5', 'pypdf==6.1.1']
template = Template().from_base_image().pip_install(PACKAGES).run_cmd(
    'python -c "import pandas, numpy, openpyxl, pypdf; print(\'office-ready\')"')
info = Template.build(template, 'webcc-office', cpu_count=1, memory_mb=1024, **E2BRuntime().options)
result = info.model_dump() if hasattr(info, 'model_dump') else vars(info)
path = Path('/var/lib/webcc-cluster/office-template.json')
path.write_text(json.dumps({'build': result, 'packages': PACKAGES}, indent=2, default=str))
path.chmod(0o600)
print(json.dumps({'built': True, 'template_id': info.template_id, 'packages': PACKAGES}), flush=True)
