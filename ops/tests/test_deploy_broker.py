"""Run the root broker with isolated paths and no real Git/service operations."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

OPS = Path(__file__).resolve().parents[1]
EXPECTED = '27f62efcb8381e34f3de895c9fc41d1613c9cfd0'
TARGET = '7e685dfa6a331a433e916a62b1d555fe04847c4c'
MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
r=pathlib.Path(os.environ['FAKE_ROOT']); name=pathlib.Path(sys.argv[0]).name
a=sys.argv[1:]; mode=os.environ.get('FAIL','')
with (r/'calls').open('a') as f: f.write(name+' '+' '.join(a)+'\n')
if name=='id': print(0)
elif name=='runuser':
    a=a[a.index('--')+1:]
    a=a[a.index('-C')+2:]
    if a[0]=='status': print(' M app.py' if mode=='dirty' else '')
    elif a[0]=='rev-parse':
        if a[1]=='FETCH_HEAD': print('f'*40 if mode=='wrong_ref' else os.environ['TARGET'])
        else: print((r/'head').read_text().strip())
    elif a[0]=='show': sys.stdout.write((r/'helper').read_text())
elif name=='curl':
    if '%{http_code}' in a: print(200)
    else: print(json.dumps({'commit':'f'*40 if mode=='postcheck' else os.environ['TARGET']}))
elif name=='systemctl': sys.exit(0)
'''


class DeployBrokerTests(unittest.TestCase):
    def fixture(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        r=Path(tmp.name)
        (r/'bin').mkdir(); (r/'backup').mkdir(); (r/'app').mkdir()
        (r/'head').write_text(EXPECTED)
        (r/'status').write_text(json.dumps({'commit':EXPECTED}))
        helper='''#!/bin/bash
echo executed > "$FAKE_ROOT/executed"
printf '%s' "$TARGET" > "$FAKE_ROOT/head"
printf '{"commit":"%s"}' "$TARGET" > "$FAKE_ROOT/status"
exit 0
'''
        (r/'helper').write_text(helper)
        config=(OPS/'trud-release-157.conf').read_text()
        original='7737fd784fa5c181c7e98a8a3465d665d9b4da324f67a11625c3848f56155a54'
        config=config.replace(original,hashlib.sha256(helper.encode()).hexdigest())
        (r/'manifest').write_text(config); (r/'manifest').chmod(0o600)
        for name in ('id','runuser','curl','systemctl'):
            p=r/'bin'/name; p.write_text(MOCK); p.chmod(0o755)
        script=(OPS/'deploy-trud-compatible.sh').read_text()
        for old,new in {'/etc/system-maintenance/trud-release.conf':r/'manifest',
                        '/opt/trud-1-site':r/'app',
                        '/var/lib/trud-1/deployment-status.json':r/'status',
                        '/var/backups':r/'backup'}.items():
            script=script.replace(old,str(new))
        # Only the isolated UID check is removed; production has no test bypass.
        script=script.replace('info.st_uid != 0 or ','')
        (r/'broker').write_text(script)
        return r

    def execute(self,r,args=(),mode=''):
        return subprocess.run(['bash',str(r/'broker'),*args],
            env={**os.environ,'PATH':str(r/'bin')+':'+os.environ['PATH'],
                 'FAKE_ROOT':str(r),'TARGET':TARGET,'FAIL':mode},
            capture_output=True,text=True,timeout=10)

    def test_fixed_action_and_legacy_exact_arguments_run_only_verified_helper(self):
        for args in ((),(TARGET,EXPECTED)):
            with self.subTest(args=args):
                r=self.fixture(); result=self.execute(r,args)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('DEPLOY_TRUD_COMPATIBLE=PASS',result.stdout)
                self.assertTrue((r/'executed').exists())
                self.assertEqual(json.loads((r/'status').read_text())['commit'],TARGET)

    def test_manifest_data_cannot_execute_shell_code(self):
        for mutation in ('shell_line','duplicate','missing','sha','branch','symlink','writable'):
            with self.subTest(mutation=mutation):
                r=self.fixture(); p=r/'manifest'; data=p.read_text()
                if mutation=='shell_line': p.write_text(data+f'touch {r}/injected\n')
                elif mutation=='duplicate': p.write_text(data+f"TARGET_SHA='{TARGET}'\n")
                elif mutation=='missing': p.write_text('\n'.join(data.splitlines()[1:]))
                elif mutation=='sha': p.write_text(data.replace(TARGET,'short'))
                elif mutation=='branch': p.write_text(data.replace('fix/release-public-atomic-20261005','$(touch '+str(r)+'/injected)'))
                elif mutation=='symlink':
                    p.rename(r/'other'); p.symlink_to(r/'other')
                elif mutation=='writable': p.chmod(0o666)
                result=self.execute(r)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse((r/'executed').exists())
                self.assertFalse((r/'injected').exists())
                self.assertNotIn('runuser',(r/'calls').read_text())

    def test_unapproved_arguments_are_rejected_before_git_access(self):
        for args in ((TARGET,),('f'*40,EXPECTED),(TARGET,'f'*40),(TARGET,EXPECTED,'extra')):
            with self.subTest(args=args):
                r=self.fixture(); result=self.execute(r,args)
                self.assertEqual(result.returncode,2)
                self.assertNotIn('runuser',(r/'calls').read_text())
                self.assertFalse((r/'executed').exists())

    def test_live_drift_dirty_tree_moved_branch_and_bad_checksum_block_execution(self):
        for mode in ('live','dirty','wrong_ref','checksum'):
            with self.subTest(mode=mode):
                r=self.fixture()
                if mode=='live': (r/'status').write_text(json.dumps({'commit':'f'*40}))
                if mode=='checksum': (r/'helper').write_text('echo unapproved\n')
                result=self.execute(r,mode=mode)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse((r/'executed').exists())

    def test_committed_postcheck_failure_is_not_reported_as_rollback(self):
        r=self.fixture(); result=self.execute(r,mode='postcheck')
        self.assertEqual(result.returncode,3,result.stderr)
        self.assertIn('COMMITTED_POSTCHECK_FAILED',result.stderr)
        self.assertEqual(json.loads((r/'status').read_text())['commit'],TARGET)
        self.assertNotIn('ROLLED_BACK',result.stderr)


if __name__=='__main__': unittest.main()
