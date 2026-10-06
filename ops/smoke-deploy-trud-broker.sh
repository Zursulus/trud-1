#!/bin/bash
set -Eeuo pipefail
[[ $(id -u) -eq 0 && $# -eq 0 ]] || { echo 'root, no arguments required' >&2; exit 2; }
SRC="$(cd "$(dirname "$0")" && pwd)"
python3 - <<'PY'
import ast, hashlib, pathlib, stat
paths = ('/usr/local/sbin/system-maintenance-worker',
         '/usr/local/bin/system-maintenance-submit',
         '/usr/local/sbin/deploy-trud-compatible',
         '/etc/system-maintenance/trud-release.conf')
for name in paths:
    info = pathlib.Path(name).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit(f'Untrusted installed file: {name}')
for name in paths[:2]:
    text = pathlib.Path(name).read_text()
    tree = ast.parse(text)
    actions = [node.value for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == 'ACTIONS' for t in node.targets)]
    expected = {'debian13-upgrade', 'apt-current-upgrade', 'reboot-host',
                'post-upgrade-finalize', 'deploy-trud-compatible'}
    if len(actions) != 1 or ast.literal_eval(actions[0]) != expected:
        raise SystemExit(f'Deploy action missing: {name}')
    compile(text, name, 'exec')
worker = pathlib.Path(paths[0]).read_text()
if hashlib.sha256(worker.encode()).hexdigest() != '19d3f246b88056fd391b54cf6d572724a3ef747ce1f9070be1a5dc0846f3c8ce':
    raise SystemExit('Reviewed fixed deploy worker mismatch.')
PY
cmp "$SRC/trud-release-157.conf" /etc/system-maintenance/trud-release.conf
cmp "$SRC/deploy-trud-compatible.sh" /usr/local/sbin/deploy-trud-compatible
test -x /usr/local/sbin/deploy-trud-compatible
bash -n /usr/local/sbin/deploy-trud-compatible
systemctl is-active --quiet system-maintenance.path
echo 'TRUD_DEPLOY_BROKER_SMOKE=PASS'
