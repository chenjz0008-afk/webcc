#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" != 0 ]; then
  echo "请使用 sudo bash deploy/install.sh"
  exit 1
fi
command -v docker >/dev/null
command -v python3 >/dev/null
project_dir="$(cd -- "$(dirname -- "$0")/.." && pwd)"
install -d -m 0755 /opt/clewdr-manager
if [ "$project_dir" != /opt/clewdr-manager ]; then
  cp "$project_dir"/manager.py "$project_dir"/updater.py "$project_dir"/serve.py "$project_dir"/egress.py /opt/clewdr-manager/
  cp -R "$project_dir"/web_tools /opt/clewdr-manager/
  cp -R "$project_dir"/static /opt/clewdr-manager/
fi
install -d -m 0700 /var/lib/clewdr-manager
if [ ! -f /etc/clewdr-manager.env ]; then
  install -m 0600 "$project_dir/manager.env.example" /etc/clewdr-manager.env
  echo "请填写 /etc/clewdr-manager.env 的两个密码后再次运行安装脚本"
  exit 1
fi
install -m 0644 "$project_dir/deploy/clewdr-manager.service" /etc/systemd/system/clewdr-manager.service
install -m 0644 "$project_dir/deploy/webcc-egress.service" /etc/systemd/system/webcc-egress.service
install -d /etc/systemd/system/docker.service.d
install -m 0644 "$project_dir/deploy/docker-egress.conf" /etc/systemd/system/docker.service.d/webcc-egress.conf
systemctl daemon-reload
systemctl enable --now webcc-egress
systemctl enable --now clewdr-manager
systemctl is-active clewdr-manager
