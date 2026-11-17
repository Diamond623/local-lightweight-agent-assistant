const assert=require('node:assert/strict'),vm=require('node:vm');
const {context,node}=require('./test_chat_ui.cjs');
const run=code=>vm.runInContext(code,context);
const pending=new Map();let listFails=false,chatCalls=0;
context.fetch=async(url,options)=>{
 const data=JSON.parse(options.body);
 if(url==='/api/messages')return new Promise(resolve=>pending.set(data.session,resolve));
 if(url==='/api/sessions'){if(listFails)throw Error('列表暂时不可用');return {ok:true,json:async()=>[]};}
 if(url==='/api/chat'){chatCalls++;return {ok:true,body:{getReader:()=>{
  let sent=false;return {read:async()=>sent?{done:true}:(sent=true,{done:false,value:new TextEncoder().encode(JSON.stringify({type:'result',answer:'已保存的完整回答',sources:[]})+'\n')}),releaseLock(){},async cancel(){}};
 }}};}
 return {ok:true,json:async()=>({})};
};
const resolve=(id,text)=>pending.get(id)({ok:true,json:async()=>[{role:'user',content:text,image:'',sources:'[]'}]});
(async()=>{
 const a=run('load(11)'),b=run('load(12)');
 node('input').value='加载时不能发出的草稿';await node('send').onclick();
 assert.equal(chatCalls,0,'Do not send into a conversation still loading');
 resolve(12,'新对话');await b;resolve(11,'过时对话');await a;
 assert.equal(run('session'),12);assert.match(node('chat').textContent,/新对话/);assert.doesNotMatch(node('chat').textContent,/过时对话/);
 assert.equal(node('send').disabled,false);
 const failed=run('load(13)');pending.get(13)({ok:false,json:async()=>({error:'读取失败'})});
 await assert.rejects(failed,/读取失败/);assert.equal(run('session'),12);assert.match(node('chat').textContent,/新对话/);
 assert.equal(node('send').disabled,false);
 listFails=true;node('input').value='正常问题';await node('send').onclick();
 assert.match(node('chat').textContent,/已保存的完整回答/);
 assert.doesNotMatch(node('chat').textContent,/本轮未完成/);
 assert.equal(node('input').value,'','A saved question must not become a retry draft');
 assert.match(node('hint').textContent,/已保存.*列表/);
 const storage=new Map();context.sessionStorage={setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)||null};
 run("currentAccountKey='aurora-view:local:1';session=12");node('input').value='返回后保留的草稿';run('saveViewState()');
 assert.equal(JSON.parse(storage.get('aurora-view:local:1')).session,12);
 assert.equal(JSON.parse(storage.get('aurora-view:local:1')).draft,'返回后保留的草稿');
 run("currentAccountKey='aurora-view:local:2'");assert.equal(run('readViewState().draft'),undefined,'Drafts remain account scoped');
 assert.match(require('node:fs').readFileSync(require('node:path').join(__dirname,'admin.html'),'utf8'),/id="backtochat" href="\/"/);
 // Invalid persisted state must not break startup restoration.
 for(const raw of ['null','[]','42','"bad"','{broken']){storage.set('aurora-view:local:2',raw);assert.equal(run('JSON.stringify(readViewState())'),'{}');}
 // A narrow drawer must close on resize, then restore the desktop choice.
 run('desktopSidebarCollapsed=false;syncSidebarViewport(true)');assert.equal(node('sidebarToggle').ariaExpanded,'false');
 run('syncSidebarViewport(false)');assert.equal(node('sidebarToggle').ariaExpanded,'true');
 run('desktopSidebarCollapsed=true;syncSidebarViewport(true);syncSidebarViewport(false)');assert.equal(node('sidebarToggle').ariaExpanded,'false');
 // Pause preference survives rebuilt welcome panels and stays account scoped.
 context.localStorage={setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)||null};
 const panel=run('createWelcomePanel()');panel.children[0].children[0].onclick();assert.match(panel.className,/stellar-paused/);
 const rebuilt=run('createWelcomePanel()');assert.match(rebuilt.className,/stellar-paused/);assert.equal(rebuilt.children[0].children[0].title,'继续星轨');
 run("currentAccountKey='aurora-view:local:3'");assert.doesNotMatch(run('createWelcomePanel()').className,/stellar-paused/);
 context.localStorage={getItem(){throw Error('storage blocked')},setItem(){throw Error('storage blocked')}};
 const blocked=run('createWelcomePanel()');blocked.children[0].children[0].onclick();assert.match(blocked.className,/stellar-paused/);
 // Right-click targets the clicked card, without switching the active chat.
 const menuCalls=[];context.fetch=async(url,options)=>{const data=JSON.parse(options.body);menuCalls.push({url,data});return {ok:true,json:async()=>url==='/api/sessions'?[{id:12,title:'当前',archived:0},{id:99,title:'另一段',archived:0}]:{ok:true}}};
 run("session=12;sessionList=[{id:12,title:'当前',archived:0},{id:99,title:'另一段',archived:0}];renderSessions()");
 const row=node('sessions').children[1];row.oncontextmenu({preventDefault(){},clientX:30,clientY:40});
 node('menurename').onclick();node('renametitle').value='新名称';await node('saverename').onclick();
 assert.equal(menuCalls.find(x=>x.url==='/api/rename-session').data.session,99);assert.equal(run('session'),12);
 row.oncontextmenu({preventDefault(){},clientX:30,clientY:40});await node('menuarchive').onclick();
 assert.equal(menuCalls.find(x=>x.url==='/api/archive-session').data.session,99);assert.equal(menuCalls.find(x=>x.url==='/api/archive-session').data.archived,true);
 run("openSessionMenu({id:99,title:'归档',archived:1},{preventDefault(){},clientX:30,clientY:40},null)");await node('menuarchive').onclick();assert.equal(menuCalls.filter(x=>x.url==='/api/archive-session').at(-1).data.archived,false);
 run("openSettingsPage('memdialog')");assert.equal(node('settingsdialog').open,true);assert.equal(node('memdialog').open,true);assert.equal(node('resourcedialog').open,false);
 run("openSettingsPage('resourcedialog')");assert.equal(node('memdialog').open,false);assert.equal(node('resourcedialog').open,true);node('closesettings').onclick();assert.equal(node('resourcedialog').open,false);
 // Brand and welcome graphic must operate the same pause state.
 context.localStorage={setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)||null};run("currentAccountKey='aurora-view:local:7'");
 const emblemPanel=run('createWelcomePanel()');node('chat').replaceChildren(emblemPanel);
 emblemPanel.children[0].children[0].onclick();assert.match(emblemPanel.className,/stellar-paused/);
 node('brandmotion').onclick();assert.doesNotMatch(emblemPanel.className,/stellar-paused/);assert.doesNotMatch(context.document.body.className,/stellar-paused/);
 node('brandmotion').onclick();assert.match(emblemPanel.className,/stellar-paused/);
 // Attachment and permission menus are mutually exclusive, dismissible, and preserve focus.
 node('tooloptions').open=true;node('attachoptions').open=true;node('attachoptions').handlers.toggle();
 assert.equal(node('tooloptions').open,false);assert.equal(node('attachoptions').open,true);
 node('tooloptions').open=true;node('tooloptions').handlers.toggle();assert.equal(node('attachoptions').open,false);
 let focusCount=0;node('toolsummary').focus=()=>focusCount++;run('closeComposerMenus(true)');
 assert.equal(node('tooloptions').open,false);assert.equal(focusCount,1);
 let imageClicks=0,documentClicks=0;node('file').click=()=>imageClicks++;node('docfile').click=()=>documentClicks++;
 node('attachoptions').open=true;node('attach').onclick();assert.equal(imageClicks,1);assert.equal(node('attachoptions').open,false);
 node('attachoptions').open=true;node('attachdoc').onclick();assert.equal(documentClicks,1);assert.equal(node('attachoptions').open,false);
 console.log('Navigation regression checks passed: out-of-order loads, loading guard, failed navigation, saved reply with sidebar failure.');
})().catch(e=>{console.error(e);process.exitCode=1});
