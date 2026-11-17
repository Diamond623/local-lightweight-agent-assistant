"""Small explicit desktop capability: foreground status and opening three apps."""
import json
from pathlib import Path
import subprocess
import sys

APPS = {'计算器': 'calculator', '文本编辑': 'textedit', '访达': 'finder'}
HELPER = Path(__file__).resolve().with_name('AuroraDesktop')

def execute(action, argument='', cancelled=lambda: False):
    if cancelled():
        import generation
        raise generation.Cancelled('已停止')
    if sys.platform != 'darwin': raise ValueError('桌面工具只支持 macOS')
    if action == 'desktop_status': args = ['status']
    elif action == 'inspect_ui': args = ['inspect']
    elif action == 'click_ui':
        try:
            target=json.loads(argument)
            index,title=target['index'],target['title']
            if type(index) is not int or not 0 <= index < 40 or not isinstance(title,str) or not 1 <= len(title) <= 80:
                raise ValueError()
            args=['click',str(index),title]
        except (ValueError,TypeError,KeyError,json.JSONDecodeError):
            raise ValueError('请先读取界面，再指定按钮的编号和完整标题') from None
    elif action == 'open_app' and argument in APPS: args = ['open', APPS[argument]]
    else: raise ValueError('仅支持授权的桌面查看、按钮点击或打开指定应用')
    result = subprocess.run([str(HELPER), *args], capture_output=True, text=True, timeout=40 if action=='click_ui' else 10)
    if result.returncode: raise ValueError('桌面工具未完成操作：'+result.stdout[:240])
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or data.get('error'): raise ValueError('桌面工具未返回有效结果')
    return json.dumps(data, ensure_ascii=False)[:1100]
