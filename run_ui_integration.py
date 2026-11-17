"""Run real HTTP UI checks with a synthetic model and an ephemeral database."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    root=Path(__file__).resolve().parent
    node=shutil.which('node')
    if not node:
        print('These development UI checks require Node.js.');return 1
    process=subprocess.Popen([sys.executable,str(root/'test_ui_server.py'),'--port','0'],cwd=root,
                             stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        banner=process.stdout.readline()
        match=re.search(r'http://127\.0\.0\.1:\d+',banner)
        if not match:raise RuntimeError('UI fixture failed to start: '+banner)
        environment=dict(os.environ,AURORA_TEST_URL=match.group(0))
        result=subprocess.run([node,str(root/'test_chat_integration.cjs')],cwd=root,env=environment,timeout=90)
        return result.returncode
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        process.stdout.close()

if __name__=='__main__':raise SystemExit(main())
