"""Execute the bootstrap against isolated files, never a real maintenance service."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

OPS = Path(__file__).resolve().parents[1]
WORKER = '''#!/usr/bin/env python3
ACTIONS={"debian13-upgrade","apt-current-upgrade","reboot-host","post-upgrade-finalize"}
def launch_exact(argv, prefix):
    return argv, prefix
def launch_reboot():
    return 'reboot'
def main(req):
    if req["action"]=="apt-current-upgrade":
        unit=launch_exact(["/fixed/apt"], "apt")
        elif_marker
'''.replace('        elif_marker', '    elif req["action"]=="reboot-host":\n        unit=launch_reboot()')
# Installer deliberately expects the existing nested dispatch indentation.
WORKER = WORKER.replace('    if req[', '        if req[').replace('        unit=launch_exact', '            unit=launch_exact').replace('    elif req[', '        elif req[').replace('        unit=launch_reboot', '            unit=launch_reboot')
MOCK = r'''#!/usr/bin/env python3
import os, pathlib, signal, subprocess, sys
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
r=pathlib.Path(os.environ['FAKE_ROOT']); mode=os.environ.get('FAIL','')
with (r/'calls').open('a') as f: f.write(name+' '+' '.join(args)+'\n')
if name=='id': print(0)
elif name=='systemctl':
    if 'system-maintenance.service' in args:
        sys.exit(0 if mode=='worker_active' else 3)
    if args[0]=='stop':
        (r/'watcher').write_text('stop')
        if mode=='signal_stop': os.kill(os.getppid(),signal.SIGTERM)
    if args[0]=='start':
        n=int((r/'starts').read_text())+1; (r/'starts').write_text(str(n))
        if (mode=='start' and n==1) or mode=='recovery': sys.exit(1)
        (r/'watcher').write_text('start')
        if mode=='signal' and n==1: os.kill(os.getppid(),signal.SIGTERM)
elif name=='install':
    if mode=='foreign_parent' and pathlib.Path(args[-2]).name=='system-maintenance-submit':
        (r/'etc/keep').write_text('external content'); sys.exit(1)
    if mode=='install:'+pathlib.Path(args[-2]).name: sys.exit(1)
    prefix=['-d'] if args[0]=='-d' else []
    sys.exit(subprocess.run(['/usr/bin/install',*prefix,*args[args.index('-m'):]]).returncode)
'''

class BrokerBootstrapTests(unittest.TestCase):
    def test_reviewed_delivery_checksums_match(self):
        result=subprocess.run(['sha256sum','-c','trud-broker-bootstrap.sha256'],
                              cwd=OPS,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def fixture(self, mode='', preinstalled=False, missing_parent=False):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        r=Path(tmp.name)
        for d in ('bin','sbin','etc','inbox','backups','run','src'):
            (r/d).mkdir()
        if missing_parent: (r/'etc').rmdir()
        self.original_worker=WORKER
        # Use exactly the publicly read submit client contract from the host.
        submit='''#!/usr/bin/env python3
import json, os, secrets, sys
from datetime import datetime, timezone
from pathlib import Path
BASE=Path("REQUEST_BASE")
INBOX=BASE/"inbox"
ACTIONS={"debian13-upgrade","apt-current-upgrade","reboot-host","post-upgrade-finalize"}
def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ACTIONS:
        raise SystemExit("usage: system-maintenance-submit debian13-upgrade|apt-current-upgrade|reboot-host|post-upgrade-finalize")
    action=sys.argv[1]
    now=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rid=f"maint-{now}-{secrets.token_hex(5)}"
    payload={"schema":1,"request_id":rid,"action":action}
    INBOX.mkdir(parents=True,exist_ok=True)
    tmp=INBOX/f".{rid}.tmp"; final=INBOX/f"{rid}.json"
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        json.dump(payload,f,sort_keys=True);f.write("\\n");f.flush();os.fsync(f.fileno())
    os.replace(tmp,final)
    print(f"REQUEST_ID={rid}")
    print(f"ACTION={action}")
    print(f"RESULT={BASE}/results/{rid}.json")
if __name__=="__main__": main()
'''.replace('REQUEST_BASE',str(r/'requests'))
        (r/'sbin/system-maintenance-worker').write_text(WORKER)
        (r/'bin/system-maintenance-submit').write_text(submit)
        (r/'watcher').write_text('start'); (r/'starts').write_text('0')
        if mode=='unknown_worker': (r/'sbin/system-maintenance-worker').write_text(WORKER.replace('ACTIONS=', 'OTHER='))
        if mode=='unknown_submit': (r/'bin/system-maintenance-submit').write_text(submit.replace('ACTIONS=', 'OTHER='))
        if mode=='no_launcher': (r/'sbin/system-maintenance-worker').write_text(WORKER.replace('def launch_exact', 'def other_launcher'))
        if mode=='pending': (r/'inbox/old.json').write_text('{}')
        if mode=='unsafe_file': (r/'sbin/system-maintenance-worker').chmod(0o666)
        if mode=='symlink':
            (r/'sbin/system-maintenance-worker').unlink()
            (r/'sbin/system-maintenance-worker').symlink_to(r/'bin/system-maintenance-submit')
        if mode=='symlink_parent':
            (r/'etc').rmdir(); (r/'etc').symlink_to(r/'bin',target_is_directory=True)
        for name in ('deploy-trud-compatible.sh','trud-release-157.conf'):
            (r/'src'/name).write_bytes((OPS/name).read_bytes())
        if mode=='manifest': (r/'src/trud-release-157.conf').write_text("TARGET_SHA='c'\n")
        if mode=='unsafe_source': (r/'src/deploy-trud-compatible.sh').chmod(0o666)
        if preinstalled:
            (r/'sbin/deploy-trud-compatible').write_text('old helper\n')
            (r/'sbin/deploy-trud-compatible').chmod(0o700)
            (r/'etc/trud-release.conf').write_text('old manifest\n')
            (r/'etc/trud-release.conf').chmod(0o600)
        for name in ('id','systemctl','install'):
            p=r/'bin'/name;p.write_text(MOCK);p.chmod(0o755)
        script=(OPS/'install-deploy-trud-broker.sh').read_text()
        replacements={'/usr/local/sbin/system-maintenance-worker':r/'sbin/system-maintenance-worker',
                      '/usr/local/sbin/deploy-trud-compatible':r/'sbin/deploy-trud-compatible',
                      '/usr/local/bin/system-maintenance-submit':r/'bin/system-maintenance-submit',
                      '/etc/system-maintenance/trud-release.conf':r/'etc/trud-release.conf',
                      '/var/lib/system-maintenance/inbox':r/'inbox',
                      '/run/trud-backup.lock':r/'run/lock','/var/backups':r/'backups'}
        for source,target in replacements.items():script=script.replace(source,str(target))
        # Test-only ownership isolation: production has no test flags/overrides.
        script=script.replace('info.st_uid != 0 or ', '')
        script=script.replace('(path.parent, *path.parent.parents)', '(path.parent,)')
        (r/'src/install.sh').write_text(script)
        return r

    def execute(self,r,mode=''):
        return subprocess.run(['bash',str(r/'src/install.sh')],
            env={**os.environ,'PATH':str(r/'bin')+':'+os.environ['PATH'],'FAKE_ROOT':str(r),'FAIL':mode},
            capture_output=True,text=True,timeout=20)

    def test_success_installs_both_sides_manifest_and_keeps_backup(self):
        r=self.fixture(); result=self.execute(r)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('INSTALL=PASS',result.stdout)
        self.assertEqual((r/'sbin/deploy-trud-compatible').read_bytes(),(OPS/'deploy-trud-compatible.sh').read_bytes())
        self.assertEqual((r/'etc/trud-release.conf').read_bytes(),(OPS/'trud-release-157.conf').read_bytes())
        self.assertEqual((r/'etc/trud-release.conf').stat().st_mode&0o777,0o600)
        self.assertEqual((r/'sbin/deploy-trud-compatible').stat().st_mode&0o777,0o700)
        submit=subprocess.run(['python3',str(r/'bin/system-maintenance-submit'),'deploy-trud-compatible'],capture_output=True,text=True)
        self.assertEqual(submit.returncode,0,submit.stderr)
        self.assertIn('ACTION=deploy-trud-compatible',submit.stdout)
        requests=list((r/'requests/inbox').glob('*.json'))
        self.assertEqual(len(requests),1)
        payload=json.loads(requests[0].read_text())
        self.assertEqual(set(payload),{'schema','request_id','action'})
        self.assertEqual(payload['action'],'deploy-trud-compatible')
        self.assertIn('REQUEST_ID='+payload['request_id'],submit.stdout)
        self.assertIn('RESULT='+str(r/'requests/results'/requests[0].name),submit.stdout)
        rejected=subprocess.run(['python3',str(r/'bin/system-maintenance-submit'),'arbitrary-root-command'],capture_output=True,text=True)
        self.assertNotEqual(rejected.returncode,0)
        self.assertEqual(len(list((r/'requests/inbox').glob('*.json'))),1)
        self.assertIn('unit=launch_exact(["'+str(r/'sbin/deploy-trud-compatible')+'"], "trud-deploy")',(r/'sbin/system-maintenance-worker').read_text())
        self.assertEqual((r/'watcher').read_text(),'start')
        self.assertTrue(list((r/'backups').glob('*/before/system-maintenance-worker')))
        self.assertNotIn('trud-1-site.service',(r/'calls').read_text())
        self.assertEqual(list((r/'inbox').iterdir()),[])

    def test_repeat_is_idempotent(self):
        r=self.fixture(); first=self.execute(r)
        self.assertEqual(first.returncode,0,first.stderr)
        content={p:p.read_bytes() for p in [r/'sbin/system-maintenance-worker',r/'bin/system-maintenance-submit',r/'sbin/deploy-trud-compatible',r/'etc/trud-release.conf']}
        second=self.execute(r)
        self.assertEqual(second.returncode,0,second.stderr)
        for p,data in content.items():self.assertEqual(p.read_bytes(),data)

    def test_missing_manifest_parent_is_created_privately(self):
        r=self.fixture(missing_parent=True); result=self.execute(r)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((r/'etc').stat().st_mode&0o777,0o700)
        self.assertEqual((r/'etc/trud-release.conf').read_bytes(),(OPS/'trud-release-157.conf').read_bytes())
        self.assertTrue(list((r/'backups').glob('*/before/manifest-parent.absent')))

    def test_readonly_smoke_requires_client_worker_and_exact_manifest(self):
        r=self.fixture(); result=self.execute(r)
        self.assertEqual(result.returncode,0,result.stderr)
        script=(OPS/'smoke-deploy-trud-broker.sh').read_text()
        for old,new in {'/usr/local/sbin/system-maintenance-worker':r/'sbin/system-maintenance-worker',
                        '/usr/local/sbin/deploy-trud-compatible':r/'sbin/deploy-trud-compatible',
                        '/usr/local/bin/system-maintenance-submit':r/'bin/system-maintenance-submit',
                        '/etc/system-maintenance/trud-release.conf':r/'etc/trud-release.conf'}.items():
            script=script.replace(old,str(new))
        script=script.replace('info.st_uid != 0 or ','')
        (r/'src/smoke.sh').write_text(script)
        env={**os.environ,'PATH':str(r/'bin')+':'+os.environ['PATH'],'FAKE_ROOT':str(r)}
        def smoke():
            return subprocess.run(['bash',str(r/'src/smoke.sh')],env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(smoke().returncode,0)
        client=r/'bin/system-maintenance-submit'; old=client.read_text()
        client.write_text(old.replace(',"deploy-trud-compatible"',''))
        self.assertNotEqual(smoke().returncode,0)
        client.write_text(old)
        (r/'etc/trud-release.conf').write_text("TARGET_SHA='wrong'\n")
        self.assertNotEqual(smoke().returncode,0)
        self.assertEqual(list((r/'inbox').iterdir()),[])
        self.assertFalse((r/'requests').exists())

    def test_preflight_failures_never_change_installed_files(self):
        for mode in ('unknown_worker','unknown_submit','no_launcher','pending','unsafe_file','unsafe_source','symlink','symlink_parent','manifest','worker_active'):
            with self.subTest(mode=mode):
                r=self.fixture(mode)
                before=(r/'bin/system-maintenance-submit').read_bytes()
                result=self.execute(r,mode)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual((r/'bin/system-maintenance-submit').read_bytes(),before)
                self.assertFalse((r/'sbin/deploy-trud-compatible').exists())
                self.assertNotIn('systemctl stop',(r/'calls').read_text())

    def test_install_start_and_signal_failures_restore_all_four_paths(self):
        for mode in ('install:deploy-trud-compatible','install:trud-release.conf',
                     'install:system-maintenance-worker','install:system-maintenance-submit','start','signal','signal_stop'):
            for installed,missing_parent in ((False,False),(False,True),(True,False)):
                with self.subTest(mode=mode,installed=installed,missing_parent=missing_parent):
                    r=self.fixture(preinstalled=installed,missing_parent=missing_parent)
                    paths=[r/'sbin/system-maintenance-worker',r/'bin/system-maintenance-submit',r/'sbin/deploy-trud-compatible',r/'etc/trud-release.conf']
                    before={p:p.read_bytes() if p.exists() else None for p in paths}
                    result=self.execute(r,mode)
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn('ROLLED_BACK',result.stderr)
                    for p,data in before.items():
                        if data is None:self.assertFalse(p.exists())
                        else:self.assertEqual(p.read_bytes(),data)
                    self.assertEqual((r/'etc').exists(),not missing_parent)
                    self.assertEqual((r/'watcher').read_text(),'start')

    def test_failed_recovery_reports_uncertain_state(self):
        r=self.fixture(); result=self.execute(r,'recovery')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('RECOVERY_UNCONFIRMED',result.stderr)
        self.assertNotIn('INSTALL=PASS',result.stdout)

    def test_recovery_preserves_foreign_content_in_new_manifest_directory(self):
        r=self.fixture(missing_parent=True); result=self.execute(r,'foreign_parent')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('RECOVERY_UNCONFIRMED',result.stderr)
        self.assertEqual((r/'etc/keep').read_text(),'external content')
        self.assertFalse((r/'etc/trud-release.conf').exists())
        self.assertEqual((r/'watcher').read_text(),'stop')

if __name__=='__main__':unittest.main()
