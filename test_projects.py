import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import projects
import agent

class ProjectTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
  self.db=sqlite3.connect(self.base/'test.sqlite3');self.db.row_factory=sqlite3.Row
  self.db.execute('CREATE TABLE sessions(id INTEGER PRIMARY KEY,title TEXT,created TEXT)');self.db.execute('INSERT INTO sessions(id) VALUES(1)');projects.setup(self.db)
  self.pid=projects.api(self.db,self.base,'create',{'name':'实验'})['id'];self.api('bind',session=1);self.db.commit()
 def tearDown(self):self.db.close();self.temp.cleanup()
 def api(self,action,**obj):return projects.api(self.db,self.base,action,{'project':self.pid,**obj})
 def propose(self,content='hello',path='notes.md'):return self.api('propose',path=path,content=content,base_revision=self.api('read',path=path)['revision'])['proposal']
 def test_preview_apply_undo_and_reject(self):
  cid=self.propose();path=projects.root_for(self.base,self.pid)/'notes.md'
  self.assertFalse(path.exists());self.assertIn('+hello',self.api('detail')['changes'][0]['diff'])
  self.api('change',id=cid,operation='apply');self.assertEqual(path.read_text(),'hello')
  self.api('change',id=cid,operation='undo');self.assertFalse(path.exists())
  cid=self.propose();self.api('change',id=cid,operation='reject');self.assertFalse(path.exists())
 def test_conflicts_never_overwrite_external_changes(self):
  path=projects.root_for(self.base,self.pid)/'notes.md';path.write_text('old');cid=self.propose('new');path.write_text('external')
  with self.assertRaises(ValueError):self.api('change',id=cid,operation='apply')
  self.assertEqual(path.read_text(),'external');path.write_text('old');self.api('change',id=cid,operation='apply');path.write_text('later')
  with self.assertRaises(ValueError):self.api('change',id=cid,operation='undo')
  self.assertEqual(path.read_text(),'later')
 def test_escape_symlink_hardlink_binary_and_size(self):
  for path in ['../escape.md','/tmp/a.md','x/../../a.md','.secret.txt','a/./x.md','a//b.md','a\\b.md','bad.exe']:
   with self.subTest(path=path),self.assertRaises(ValueError):self.propose(path=path)
  root=projects.root_for(self.base,self.pid);(root/'link').symlink_to(self.base,target_is_directory=True)
  with self.assertRaises(ValueError):self.propose(path='link/a.md')
  outside=self.base/'outside.txt';outside.write_text('secret');(root/'hard.txt').hardlink_to(outside)
  with self.assertRaises(ValueError):self.propose(path='hard.txt')
  (root/'bin.txt').write_bytes(b'a\x00b')
  with self.assertRaises(ValueError):self.api('read',path='bin.txt')
  with self.assertRaises(ValueError):self.propose('x'*100001)
  self.assertEqual(outside.read_text(),'secret')
 def test_durable_tasks_and_account_isolation(self):
  task=projects.begin(self.db,1,'写笔记');projects.event(self.db,task['id'],{'summary':'计划'});projects.finish(self.db,task['id'],'waiting_review','等待审核');self.db.commit()
  self.db.close();self.db=sqlite3.connect(self.base/'test.sqlite3');self.db.row_factory=sqlite3.Row
  data=self.api('detail');self.assertEqual(data['tasks'][0]['status'],'waiting_review');self.assertEqual(json.loads(data['tasks'][0]['events'])[0]['summary'],'计划')
  other=sqlite3.connect(':memory:');other.row_factory=sqlite3.Row;projects.setup(other)
  with self.assertRaises(ValueError):projects.project(other,self.pid)
  other.close()
 def test_agent_reads_before_proposing_and_does_not_write(self):
  path=projects.root_for(self.base,self.pid)/'notes.md';path.write_text('old');task=projects.begin(self.db,1,'改文件')
  with self.assertRaises(ValueError):projects.execute(self.db,self.base,task,'propose_file',{'path':'notes.md','content':'new'})
  projects.execute(self.db,self.base,task,'read_file',{'path':'notes.md'})
  actions=iter([json.dumps({'action':'propose_file','path':'notes.md','content':'new'}),'{"action":"finish"}'])
  result=agent.run('改文件',lambda _:next(actions),None,False,[],[],lambda:False,lambda _:None,workspace=lambda action,obj:projects.execute(self.db,self.base,task,action,obj))
  self.assertEqual(path.read_text(),'old');self.assertIn('尚未应用',result['trace'][0]['summary']);self.assertEqual(len(self.api('detail')['changes']),1)
  path.write_text('external')
  with self.assertRaises(ValueError):projects.execute(self.db,self.base,task,'propose_file',{'path':'notes.md','content':'new'})
 def test_project_search_stays_bounded_and_ignores_hidden_links_and_other_accounts(self):
  root=projects.root_for(self.base,self.pid);(root/'src').mkdir()
  (root/'src'/'main.py').write_text('def Greeting():\n    return "AURORA_MATCH"\n')
  (root/'.hidden.py').write_text('AURORA_MATCH')
  (root/'linked.py').symlink_to(self.base/'outside.py')
  (self.base/'outside.py').write_text('AURORA_MATCH')
  (root/'hard.py').hardlink_to(self.base/'outside.py')
  task=projects.begin(self.db,1,'查找代码')
  result=json.loads(projects.execute(self.db,self.base,task,'search_files',{'query':'aurora_match'}))
  self.assertEqual([row['path'] for row in result['matches']],['src/main.py'])
  self.assertEqual(result['matches'][0]['line'],2)
  with self.assertRaises(ValueError):projects.execute(self.db,self.base,task,'search_files',{'query':'a'})
  with self.assertRaises(ValueError):projects.execute(self.db,self.base,task,'search_files',{'query':'x'*81})
 def test_static_validation_does_not_execute_code_and_rejects_invalid_agent_proposals(self):
  root=projects.root_for(self.base,self.pid);task=projects.begin(self.db,1,'修正语法')
  marker=root/'executed.txt'
  code=f'open({str(marker)!r},"w").write("bad")\n'
  parsed=json.loads(projects.execute(self.db,self.base,task,'check_code',{'path':'script.py','content':code}))
  self.assertEqual(parsed['status'],'passed');self.assertFalse(marker.exists())
  for name,content in [('script.py','def f(:\n pass'),('data.json','{"broken": }'),('config.toml','key = [')]:
   report=json.loads(projects.execute(self.db,self.base,task,'check_code',{'path':name,'content':content}))
   self.assertEqual(report['status'],'failed')
   with self.assertRaisesRegex(ValueError,'语法错误'):
    projects.execute(self.db,self.base,task,'propose_file',{'path':name,'content':content})
  self.assertEqual(self.api('detail')['changes'],[])
 def test_agent_can_search_check_recover_and_leave_a_reviewable_change(self):
  root=projects.root_for(self.base,self.pid);(root/'main.py').write_text('ANSWER = 1\n')
  task=projects.begin(self.db,1,'把 ANSWER 改为 2')
  actions=iter([{'action':'search_files','query':'ANSWER'},
                {'action':'read_file','path':'main.py'},
                {'action':'check_code','path':'main.py','content':'ANSWER = ('},
                {'action':'check_code','path':'main.py','content':'ANSWER = 2\n'},
                {'action':'propose_file','path':'main.py','content':'ANSWER = 2\n'},
                {'action':'finish'}])
  result=agent.run('把 ANSWER 改为 2',lambda _:json.dumps(next(actions)),None,False,[],[],
                   lambda:False,lambda _:None,
                   workspace=lambda action,obj:projects.execute(self.db,self.base,task,action,obj))
  self.assertEqual([x['tool'] for x in result['trace']],['search_files','read_file','check_code','check_code','propose_file'])
  self.assertEqual([x['status'] for x in result['trace']],['completed','completed','error','completed','completed'])
  self.assertEqual(result['review']['status'],'checked')
  self.assertEqual((root/'main.py').read_text(),'ANSWER = 1\n')
  self.assertIn('+ANSWER = 2',self.api('detail')['changes'][0]['diff'])
 def test_large_file_paged_read_and_unique_patch_waits_for_review(self):
  root=projects.root_for(self.base,self.pid);path=root/'main.py'
  original=''.join(f'VALUE_{n} = {n}\n' for n in range(400))
  path.write_text(original)
  task=projects.begin(self.db,1,'把第 320 行改为 999')
  first=json.loads(projects.execute(self.db,self.base,task,'read_file',{'path':'main.py','start_line':310,'limit':30}))
  self.assertEqual(first['start_line'],310)
  self.assertIn('VALUE_320 = 320',first['text'])
  self.assertEqual(first['total_lines'],400)
  self.assertTrue(first['truncated'])
  result=json.loads(projects.execute(self.db,self.base,task,'propose_patch',{'path':'main.py','old':'VALUE_320 = 320','new':'VALUE_320 = 999'}))
  self.assertEqual(result['check']['status'],'passed')
  self.assertEqual(path.read_text(),original)
  self.assertIn('+VALUE_320 = 999',self.api('detail')['changes'][0]['diff'])
  self.api('change',id=result['proposal'],operation='apply')
  self.assertIn('VALUE_320 = 999',path.read_text())
 def test_patch_rejects_unread_duplicate_stale_and_invalid_changes(self):
  root=projects.root_for(self.base,self.pid);path=root/'script.py';path.write_text('# unique\n'+'def fn():\n    return 1\n'*150)
  task=projects.begin(self.db,1,'改代码')
  args={'path':'script.py','old':'return 1','new':'return 2'}
  with self.assertRaisesRegex(ValueError,'尚未读取'):projects.execute(self.db,self.base,task,'propose_patch',args)
  projects.execute(self.db,self.base,task,'read_file',{'path':'script.py'})
  with self.assertRaisesRegex(ValueError,'恰好出现一次'):projects.execute(self.db,self.base,task,'propose_patch',args)
  with self.assertRaisesRegex(ValueError,'语法错误'):
   projects.execute(self.db,self.base,task,'propose_patch',{'path':'script.py','old':'# unique\n','new':'def (\n'})
  path.write_text(path.read_text()+'# external\n')
  with self.assertRaisesRegex(ValueError,'版本已变化'):projects.execute(self.db,self.base,task,'propose_patch',{'path':'script.py','old':'def fn():','new':'def other():'})
  self.assertEqual(self.api('detail')['changes'],[])
  with self.assertRaises(ValueError):projects.execute(self.db,self.base,task,'read_file',{'path':'../secret.py'})
 def test_agent_uses_paged_read_then_proposes_a_large_file_patch(self):
  root=projects.root_for(self.base,self.pid);path=root/'main.py'
  path.write_text(''.join(f'line_{n} = {n}\n' for n in range(350)))
  task=projects.begin(self.db,1,'修改第 250 行')
  actions=iter([{'action':'search_files','query':'line_250'},
                {'action':'read_file','path':'main.py','start_line':245,'limit':12},
                {'action':'propose_patch','path':'main.py','old':'line_250 = 250','new':'line_250 = 999'},
                {'action':'finish'}])
  result=agent.run('修改第 250 行',lambda _:json.dumps(next(actions)),None,False,[],[],lambda:False,lambda _:None,
                   workspace=lambda action,obj:projects.execute(self.db,self.base,task,action,obj))
  self.assertEqual([x['tool'] for x in result['trace']],['search_files','read_file','propose_patch'])
  self.assertEqual(result['review']['status'],'checked')
  self.assertIn('line_250 = 250',path.read_text())
  self.assertEqual(self.api('detail')['changes'][0]['status'],'pending')
 def test_agent_cannot_enable_own_workspace(self):
  actions=iter(['{"action":"propose_file","path":"a.md","content":"evil"}','{"action":"finish"}'])
  result=agent.run('写文件',lambda _:next(actions),None,False,[],[],lambda:False,lambda _:None)
  self.assertEqual(result['trace'][0]['status'],'skipped');self.assertFalse(list(projects.root_for(self.base,self.pid).iterdir()))
 def test_restart_unbind_and_nested_files(self):
  self.api('update',goal='目标',progress='一半');self.assertEqual(self.api('detail')['project']['progress'],'一半')
  cid=self.propose('print(1)','src/main.py');self.api('change',id=cid,operation='apply')
  task=projects.begin(self.db,1,'列表');data=json.loads(projects.execute(self.db,self.base,task,'list_files',{'path':'src'}));self.assertEqual(data[0]['path'],'main.py')
  with self.assertRaises(ValueError):projects.execute(self.db,self.base,task,'list_files',{'path':'../'})
  self.db.commit();projects.INITIALIZED.clear();projects.setup(self.db)
  self.assertEqual(self.api('detail')['tasks'][0]['status'],'interrupted')
  self.api('unbind',session=1);self.assertIsNone(projects.begin(self.db,1,'无项目'))



 def test_stale_editor_and_missing_revision_cannot_create_proposal(self):
  path=projects.root_for(self.base,self.pid)/'notes.md';path.write_text('original')
  original=self.api('read',path='notes.md')
  with self.assertRaises(ValueError):self.api('propose',path='notes.md',content='edited without reading')
  path.write_text('external edit')
  with self.assertRaises(ValueError):self.api('propose',path='notes.md',content='stale edit',base_revision=original['revision'])
  self.assertEqual(self.api('detail')['changes'],[]);self.assertEqual(path.read_text(),'external edit')
 def test_all_changes_handled_updates_task_but_not_failures(self):
  task=projects.begin(self.db,1,'two files')
  first=projects.propose(self.db,self.base,self.pid,'one.md','one',task['id'])['proposal']
  second=projects.propose(self.db,self.base,self.pid,'two.md','two',task['id'])['proposal']
  projects.finish(self.db,task['id'],'waiting_review','审核修改')
  self.api('change',id=first,operation='apply');self.assertEqual(self.api('detail')['tasks'][0]['status'],'waiting_review')
  self.api('change',id=second,operation='reject');self.assertEqual(self.api('detail')['tasks'][0]['status'],'reviewed')
  projects.finish(self.db,task['id'],'failed','model error');self.api('change',id=first,operation='undo');self.assertEqual(self.api('detail')['tasks'][0]['status'],'failed')
 def test_failed_write_does_not_leave_change_stuck(self):
  from unittest.mock import patch
  cid=self.propose();self.db.commit()
  with self.assertRaises(OSError),patch.object(projects,'atomic_write',side_effect=OSError('disk full')):
   with self.db:self.api('change',id=cid,operation='apply')
  self.assertEqual(self.api('detail')['changes'][0]['status'],'pending')
  self.api('change',id=cid,operation='apply');self.assertEqual(self.api('read',path='notes.md')['text'],'hello')
 def test_new_chat_does_not_rebind_existing_chat(self):
  sid=self.api('new-chat')['id'];self.assertNotEqual(sid,1)
  self.assertEqual(projects.session_project(self.db,sid)['id'],self.pid)
  self.assertEqual(projects.session_project(self.db,1)['id'],self.pid)
 def test_diff_readable_when_no_final_newline(self):
  path=projects.root_for(self.base,self.pid)/'notes.md';path.write_text('old');self.propose('new')
  diff=self.api('detail')['changes'][0]['diff'];self.assertIn('-old\n',diff);self.assertIn('+new\n',diff);self.assertNotIn('-old+new',diff)
 def test_duplicate_agent_proposal_is_not_repeated(self):
  task=projects.begin(self.db,1,'写文件');action=json.dumps({'action':'propose_file','path':'notes.md','content':'one'})
  actions=iter([action,action,'{"action":"finish"}'])
  result=agent.run('写文件',lambda _:next(actions),None,False,[],[],lambda:False,lambda _:None,workspace=lambda a,o:projects.execute(self.db,self.base,task,a,o))
  self.assertEqual(len(self.api('detail')['changes']),1);self.assertEqual(result['trace'][1]['status'],'skipped')

class ProjectHTTPTests(unittest.TestCase):
 def test_authenticated_agent_proposal_review_and_account_scope(self):
  import threading,urllib.request,urllib.error
  from http.server import ThreadingHTTPServer
  from unittest.mock import patch
  import app,auth,generation
  with tempfile.TemporaryDirectory() as temp,patch.object(app,'DATA',Path(temp)):
   server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   def post(action,data=None,cookie=''):
    request=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/'+action,json.dumps(data or {}).encode(),headers={'X-Local-Token':app.TOKEN,'Cookie':cookie,'Content-Type':'application/json'})
    with urllib.request.urlopen(request) as response:return json.load(response),response.headers.get('Set-Cookie')
   try:
    with self.assertRaises(urllib.error.HTTPError) as error:post('projects')
    self.assertEqual(error.exception.code,401)
    _,owner=post('auth/register',{'username':'owner','password':'password-12345','display_name':'测试'})
    sid=post('new',cookie=owner)[0]['id'];pid=post('projects',{'action':'create','name':'HTTP测试'},owner)[0]['id']
    post('projects',{'action':'bind','project':pid,'session':sid},owner)
    outputs=iter(['{"action":"propose_file","path":"notes.md","content":"verified"}','{"action":"finish"}'])
    def planner(*args,**kwargs):return {'choices':[{'message':{'content':next(outputs)}}]}
    def answer(*args,**kwargs):return {'choices':[{'message':{'content':'建议已生成，等待审核'},'finish_reason':'stop'}]}
    with patch.object(app,'start_model'),patch.object(app,'model_info',return_value='fixture'),patch.object(generation,'request_json',side_effect=planner),patch.object(app,'request_json',side_effect=answer),patch.object(app,'schedule_summary'),patch('memory_worker.schedule'):
     result=post('chat',{'session':sid,'text':'创建 notes.md','mode':'gemma','online':False,'agent':True},owner)[0]
    detail=post('projects',{'action':'detail','project':pid},owner)[0]
    self.assertEqual(detail['tasks'][0]['status'],'waiting_review');self.assertEqual(len(detail['changes']),1)
    path=Path(detail['directory'])/'notes.md';self.assertFalse(path.exists());cid=detail['changes'][0]['id']
    _,guest=post('auth/register',{'username':'guest','password':'password-12345','display_name':'访客'})
    self.assertEqual(post('projects',{'action':'list'},guest)[0]['projects'],[])
    with self.assertRaises(urllib.error.HTTPError):post('projects',{'action':'change','id':cid,'operation':'apply'},guest)
    post('projects',{'action':'change','id':cid,'operation':'apply'},owner);self.assertEqual(path.read_text(),'verified')
    post('projects',{'action':'change','id':cid,'operation':'undo'},owner);self.assertFalse(path.exists())
   finally:server.shutdown();server.server_close();thread.join()

if __name__=="__main__":unittest.main()
