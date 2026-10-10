"""Persistent proxy-only Docker egress, independent of the upstream image."""
import fcntl
import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


class EgressError(RuntimeError):
    pass


def run(args, data=None):
    result = subprocess.run(args, input=data, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise EgressError('出口限制操作失败：' + args[0])
    return result.stdout


def endpoint(proxy):
    parsed = urlsplit(proxy)
    try:
        address = ipaddress.IPv4Address(parsed.hostname)
        port = parsed.port
    except (ValueError, TypeError):
        raise EgressError('出口白名单要求代理使用固定 IPv4 地址') from None
    if parsed.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not port:
        raise EgressError('代理协议或端口无效')
    if not address.is_global:
        raise EgressError('出口代理必须使用公网 IPv4 地址')
    return str(address), port


def names(identity):
    if not re.fullmatch('[a-f0-9]{12}', identity):
        raise EgressError('账号网络标识无效')
    return 'webcc-' + identity, 'wc' + identity


def rules(policies, ipv6=False):
    lines = ['*filter', ':WEBCC-OUT - [0:0]', ':WEBCC-HOST - [0:0]',
             '-F WEBCC-OUT', '-F WEBCC-HOST']
    if not ipv6:
        for chain in ['WEBCC-OUT', 'WEBCC-HOST']:
            lines.append('-A ' + chain + ' -i wc+ -m conntrack --ctstate ESTABLISHED,RELATED --ctdir REPLY -j ACCEPT')
        for identity, policy in sorted(policies.items()):
            _, bridge = names(identity)
            address, port = endpoint('http://' + policy['ip'] + ':' + str(policy['port']))
            lines.append(f'-A WEBCC-OUT -i {bridge} -d {address}/32 -p tcp --dport {port} -j ACCEPT')
    for chain in ['WEBCC-OUT', 'WEBCC-HOST']:
        lines.extend(['-A ' + chain + ' -i wc+ -j DROP', '-A ' + chain + ' -j RETURN'])
    return '\n'.join(lines + ['COMMIT', ''])


def apply(policies):
    for command in ['iptables', 'ip6tables']:
        if subprocess.run([command, '-w', '5', '-S', 'DOCKER-USER'], capture_output=True).returncode:
            run([command, '-w', '5', '-N', 'DOCKER-USER'])
        run([command + '-restore', '--wait', '5', '--noflush'], rules(policies, command == 'ip6tables'))
        for parent, target in [('DOCKER-USER', 'WEBCC-OUT'), ('FORWARD', 'WEBCC-OUT'), ('INPUT', 'WEBCC-HOST')]:
            if subprocess.run([command, '-w', '5', '-C', parent, '-j', target], capture_output=True).returncode:
                run([command, '-w', '5', '-I', parent, '1', '-j', target])


def locked(data, change=None):
    data = Path(data)
    with (data / 'egress.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = data / 'egress.json'
        policies = json.loads(path.read_text()) if path.exists() else {}
        if change:
            policies.update(change)
            temp = path.with_suffix('.tmp')
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump(policies, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, path)
        apply(policies)


def prepare(data, identity, proxy):
    address, port = endpoint(proxy)
    network, bridge = names(identity)
    locked(data, {identity: {'ip': address, 'port': port}})
    inspected = subprocess.run(['docker', 'network', 'inspect', network], capture_output=True, text=True)
    if inspected.returncode:
        run(['docker', 'network', 'create', '--driver', 'bridge', '--label', 'webcc-egress=true',
             '--opt', 'com.docker.network.bridge.name=' + bridge, network])
    else:
        value = json.loads(inspected.stdout)[0]
        if value.get('EnableIPv6') or value.get('Driver') != 'bridge' or value.get('Options', {}).get('com.docker.network.bridge.name') != bridge:
            raise EgressError('账号网络与出口策略不一致')
    return ['--network', network, '--dns', '127.0.0.1', '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1']


if __name__ == '__main__':
    import sys
    locked(sys.argv[1] if len(sys.argv) == 2 else '/var/lib/clewdr-manager')
