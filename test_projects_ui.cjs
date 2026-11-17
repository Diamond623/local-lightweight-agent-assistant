const assert=require('node:assert/strict'),vm=require('node:vm');
const {context,node}=require('./test_chat_ui.cjs');
const run=s=>vm.runInContext(s,context),calls=[];
const fixture={project:{id:1,name:'测试',goal:'目标',progress:'进度'},directory:'/test/workspaces/1',files:[{path:'notes.md'}],changes:[{id:2,path:'<img src=x>.md',status:'pending',diff:'<script>bad()</script>'}],tasks:[{id:3,session_id:7,question:'创建笔记',status:'waiting_review',events:JSON.stringify([{summary:'待审核'}]),result:'待审核'}]};
context.fetch=async(url,opts)=>{const obj=JSON.parse(opts.body);calls.push({url,obj});let data={};if(url==='/api/projects'){data=obj.action==='list'?{projects:[fixture.project],current:fixture.project}:obj.action==='detail'?fixture:obj.action==='read'?{text:'safe',revision:'r1'}:{ok:true};}return{ok:true,json:async()=>data};};
(async()=>{
 await run('projectRefresh()');assert.equal(node('projectselect').children.length,1);assert.match(node('projectcurrent').textContent,/测试/);
 const change=node('projectchanges').children[0];assert.equal(change.children[1].textContent,'<script>bad()</script>');assert.equal(calls.filter(c=>c.obj.action==='change').length,0,'Rendering must never apply a change');
 await change.children[2].onclick();assert.equal(calls.filter(c=>c.obj.operation==='apply').length,1);
 node('projectpath').value='notes.md';await run('projectRead()');assert.equal(node('projectcontent').value,'safe');
 const task=node('projecttasks').children[0];assert.match(task.textContent,/等待审核/);assert.equal(calls.filter(c=>c.url==='/api/chat').length,0);
 assert.equal(node('projectstop').disabled,true);
 // Refresh must not discard unsaved project fields or editor text.
 node('projectgoal').value='未保存目标';node('projectgoal').oninput();
 node('projectprogress').value='未保存进度';node('projectprogress').oninput();
 node('projectcontent').value='local edit';node('projectcontent').oninput();
 await run('projectRefresh()');assert.equal(node('projectgoal').value,'未保存目标');assert.equal(node('projectprogress').value,'未保存进度');assert.equal(node('projectcontent').value,'local edit');
 await node('projectpropose').onclick();const proposal=calls.find(c=>c.obj.action==='propose');assert.equal(proposal.obj.base_revision,'r1');assert.equal(proposal.obj.content,'local edit');
 const normalFetch=context.fetch;
 // A late read from project 1 cannot populate project 2, or overwrite typing.
 let resolveRead;context.fetch=async(url,opts)=>{const o=JSON.parse(opts.body);if(o.action==='read')return new Promise(resolve=>{resolveRead=()=>resolve({ok:true,json:async()=>({text:'stale',revision:'old'})})});return normalFetch(url,opts)};
 const pending=run('projectRead()');run('selectedProject=2;projectRestoreFile()');node('projectpath').value='other.md';node('projectcontent').value='project two draft';node('projectcontent').oninput();resolveRead();await pending;assert.equal(node('projectcontent').value,'project two draft');
 run('selectedProject=1;projectRestoreFile()');assert.equal(node('projectcontent').value,'local edit');
 const pending2=run('projectRead()');node('projectcontent').value='typing while read';node('projectcontent').oninput();resolveRead();await pending2;assert.equal(node('projectcontent').value,'typing while read');
 context.fetch=normalFetch;
 // No metadata race may drop text entered while save is in flight.
 let resolveSave;context.fetch=async(url,opts)=>{const o=JSON.parse(opts.body);if(o.action==='update')return new Promise(resolve=>{resolveSave=()=>resolve({ok:true,json:async()=>({ok:true})})});return normalFetch(url,opts)};
 const saving=node('projectsave').onclick();node('projectgoal').value='保存期间继续输入';node('projectgoal').oninput();resolveSave();await saving;context.fetch=normalFetch;await run('projectDetail()');assert.equal(node('projectgoal').value,'保存期间继续输入');
 // A file path can be typed before reading; it is not an unsaved content edit.
 run('selectedProject=3;projectRestoreFile()');node('projectpath').value='typed.md';node('projectpath').oninput();await run('projectRead()');assert.equal(node('projectcontent').value,'safe');
 run('attachmentBusy=true');await node('projectnewchat').onclick();assert.equal(calls.filter(c=>c.obj.action==='new-chat').length,0,'Attachment load must not rebind an existing chat');run('attachmentBusy=false;projectControls()');
 console.log('Project UI passed: explicit apply, safe diff rendering, file revisions, drafts, stale reads, save races and task status.');
})().catch(e=>{console.error(e);process.exitCode=1});
