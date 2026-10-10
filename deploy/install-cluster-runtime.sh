#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" != 0 ]; then
  echo "请使用 sudo bash deploy/install-cluster-runtime.sh"
  exit 1
fi
project_dir="$(cd -- "$(dirname -- "$0")/.." && pwd)"
install -d -m 0700 /var/lib/webcc-cluster
python3 -m venv /var/lib/webcc-cluster/venv
/var/lib/webcc-cluster/venv/bin/pip install -r "$project_dir/requirements/cluster.txt" -r "$project_dir/requirements/web-tools.txt" -r "$project_dir/requirements/files.txt" -r "$project_dir/requirements/node.txt" -r "$project_dir/requirements/runtime.txt"
install -d /etc/systemd/system/clewdr-manager.service.d
cat > /etc/systemd/system/clewdr-manager.service.d/cluster.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/var/lib/webcc-cluster/venv/bin/python /opt/clewdr-manager/serve.py
EOF
systemctl daemon-reload
echo "运行时已安装；迁移、数据库配置和服务重启按 POSTGRESQL-MIGRATION.md 执行。"
