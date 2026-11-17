"""Install a user-owned launchd task. No elevated privileges required."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import subprocess
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
LABEL='local.aurora.daily-briefing'


def prepare():
    runtime=ROOT/'tasks-data';runtime.mkdir(exist_ok=True,mode=0o700)
    return {'Label':LABEL,'ProgramArguments':['/opt/homebrew/bin/python3',str(ROOT/'daily_briefing.py')],
        'WorkingDirectory':str(ROOT),'RunAtLoad':True,'StartInterval':60,
        'StandardOutPath':str(runtime/'scheduler.log'),'StandardErrorPath':str(runtime/'scheduler-error.log'),
        'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}


def install(session_only=False):
    values=prepare()
    folder=ROOT if session_only else Path.home()/'Library/LaunchAgents'
    folder.mkdir(parents=True,exist_ok=True)
    dest=folder/(LABEL+'.plist');dest.write_bytes(plistlib.dumps(values))
    domain='gui/'+str(os.getuid())
    # Replacing this task is safe and does not affect other agents.
    subprocess.run(['/bin/launchctl','bootout',domain+'/'+LABEL],capture_output=True,timeout=10,check=False)
    result=subprocess.run(['/bin/launchctl','bootstrap',domain,str(dest)],capture_output=True,text=True,timeout=15)
    if result.returncode: raise RuntimeError(result.stderr.strip() or '系统未能注册任务')
    check=subprocess.run(['/bin/launchctl','print',domain+'/'+LABEL],capture_output=True,text=True,timeout=10)
    if check.returncode: raise RuntimeError('无法确认任务已注册')
    print('后台任务已注册。请在 Aurora 的“定时任务”中设置时间、内容与开关。')
    if session_only: print('仅当前登录会话有效；双击 Install-Daily-Briefing.command 后可跨登录保留。')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--session-only',action='store_true');args=parser.parse_args()
    install(args.session_only)
