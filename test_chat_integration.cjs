// Real HTTP frontend/backend integration, with test_ui_server.py's synthetic model.
const assert=require('node:assert/strict'),vm=require('node:vm');
const {context,node}=require('./test_chat_ui.cjs');
const base=process.env.AURORA_TEST_URL||'http://127.0.0.1:18766';
(async()=>{
 const html=await(await fetch(base+'/')).text(),token=html.match(/const token='([^']+)'/)[1];
 const registered=await fetch(base+'/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify({username:'integration',display_name:'测试','password':'integration-password-123'})});assert.equal(registered.status,200);const cookie=registered.headers.get('set-cookie').split(';')[0];
 context.fetch=(url,options)=>fetch(base+url,{...options,headers:{...options.headers,'X-Local-Token':token,'Cookie':cookie}});
 async function post(path,data){const response=await context.fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Error(result.error);return result;}
 const {id}=await post('new',{});vm.runInContext('session='+id,context);node('mode').value='gemma';node('online').checked=true;
 async function ask(text){node('input').value=text;await node('send').onclick();const replies=node('chat').children.filter(n=>n.className==='message assistant');return replies.at(-1);}
 assert.match((await ask('记住：我叫星河')).textContent,/已记住/);
 assert.match((await ask('你知道我是谁吗')).textContent,/星河/);
 assert.match((await ask('广州今天天气怎么样')).textContent,/本轮搜索：广州 今天天气/);
 assert.match((await ask('明天呢')).textContent,/本轮搜索：广州 明天天气/);
 const switched=await ask('珠海呢');assert.match(switched.textContent,/本轮搜索：珠海 明天天气/);
 assert.doesNotMatch(switched._rawText,/广州/);
 const creative=await ask('给你自己写一段推广词');assert.equal(creative._rawText,'Aurora，让灵感有下文。');assert.doesNotMatch(creative.textContent,/地点|本轮搜索/);
 const document=await post('document',{name:'notes.txt',data:Buffer.from('AURORA_TEST_DOCUMENT 归结法课程笔记').toString('base64')});
 vm.runInContext('attachedDoc='+JSON.stringify(document),context);
 assert.match((await ask('概括这份文档'))._rawText,/AURORA_TEST_DOCUMENT/);
 assert.equal(vm.runInContext('attachedDoc.name',context),'notes.txt');
 vm.runInContext("attachedDoc=null;attached='data:image/png;base64,aGVsbG8='",context);node('attachment').textContent='fixture.png';
 assert.match((await ask('看看这张图'))._rawText,/测试图片/);
 assert.ok(vm.runInContext('attached',context));assert.match((await ask('继续看这张图'))._rawText,/测试图片/);
 vm.runInContext("attached='';attachedDoc=null",context);
 const before=(await post('messages',{session:id})).length;
 node('input').value='写一段长回答';const running=node('send').onclick();
 for(let i=0;i<100;i++){const last=node('chat').children.filter(n=>n.className==='message assistant').at(-1);if(last?._rawText.includes('用于检查停止'))break;await new Promise(resolve=>setTimeout(resolve,20));}
 assert.equal(vm.runInContext('busy',context),true);node('input').value='保留草稿';await node('stop').onclick();await running;
 assert.equal(node('input').value,'保留草稿');assert.equal((await post('messages',{session:id})).length,before);
 assert.equal(vm.runInContext('busy',context),false);
 await post('memory-settings',{enabled:true});
 await ask('我的专业是人工智能。');let autoNotes=await post('memories',{});assert.ok(autoNotes.some(n=>n.origin==='auto'&&n.content.includes('人工智能')));
 await ask('更正，现在我的专业是计算机科学。');autoNotes=await post('memories',{});assert.ok(autoNotes.some(n=>n.origin==='auto'&&n.content.includes('计算机科学')));assert.ok(!autoNotes.some(n=>n.origin==='auto'&&n.content.includes('人工智能')));
 await post('memory-settings',{enabled:false});await ask('我的学校是电子科技大学。');assert.ok(!(await post('memories',{})).some(n=>n.origin==='auto'&&n.content.includes('电子科技大学')));
 node('online').checked=false;await node('online').onchange();assert.equal((await post('preferences',{})).online,false);
 await node('tasks').onclick();assert.equal(node('taskdialog').open,true);
 node('taskname').value='每日练习';node('taskprompt').value='提醒我复习数组';node('taskkind').value='reminder';node('taskrepeat').value='daily';node('taskclock').value='09:15';node('taskdate').value='';
 await node('savetask').onclick();assert.match(node('taskfeedback').textContent,/已保存/);
 assert.match(node('tasklist').textContent,/每日练习/);assert.match(node('tasklist').textContent,/09:15/);
 const task=(await post('tasks',{})).tasks.find(t=>t.name==='每日练习');assert.equal(task.prompt,'提醒我复习数组');
 await post('toggle-task',{id:task.id});assert.equal((await post('tasks',{})).tasks.find(t=>t.id===task.id).enabled,0);
 await post('delete-task',{id:task.id});assert.equal((await post('tasks',{})).tasks.some(t=>t.id===task.id),false);
 node('closetasks').onclick();assert.equal(node('taskdialog').open,false);
 console.log('Real HTTP UI integration passed: explicit and automatic memory, updates, opt-out, identity, Guangzhou→tomorrow→Zhuhai→new creative task, document, image followups, streamed stop, draft preservation and preferences.');
})().catch(e=>{console.error(e);process.exitCode=1;});
