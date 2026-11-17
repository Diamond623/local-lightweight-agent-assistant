const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {context,node}=require('./test_chat_ui.cjs');
const html=fs.readFileSync(path.join(__dirname,'index.html'),'utf8');
(async()=>{
 for(const mode of ['qwen27b','qwen35','gemma'])assert.ok(html.includes(`<option value="${mode}"`));
 for(const mode of ['text','vision'])assert.ok(!html.includes(`<option value="${mode}"`));
 const calls=[];let blocked=false,preferenceMode='gemma';
 context.fetch=async(url,options)=>{
  const data=JSON.parse(options.body);calls.push({url,data});
  if(url==='/api/info')return {ok:true,json:async()=>({build:'test',auto_modes:['gemma','qwen27b']})};
  if(url==='/api/auth/state')return {ok:true,json:async()=>({user:{owner:true,display_name:'test',username:'test'}})};
  if(url==='/api/preferences')return {ok:true,json:async()=>({mode:preferenceMode,auto_start:true,online:false})};
  if(url==='/api/start')return blocked?{ok:false,json:async()=>({error:'8084 有非本次 Aurora 管理的服务'})}:{ok:true,json:async()=>({message:'模型已经就绪（复用现有服务）'})};
  if(url==='/api/status')return {ok:true,json:async()=>({qwen27b:{ready:true},gemma:{ready:false},text:{ready:false},vision:{ready:false},qwen35:{ready:false}})};
  if(url==='/api/sessions')return {ok:true,json:async()=>[{id:7,title:'existing history'}]};
  if(url==='/api/messages')return {ok:true,json:async()=>[{role:'user',content:'history preserved',image:'',sources:'[]'}]};
  return {ok:true,json:async()=>({})};
 };
 node('mode').value='qwen27b';await node('mode').onchange();
 assert.ok(calls.some(c=>c.url==='/api/start'&&c.data.mode==='qwen27b'));
 assert.ok(calls.some(c=>c.url==='/api/preferences'&&c.data.mode==='qwen27b'));
 assert.match(node('status').textContent,/模型就绪/);assert.equal(node('mode').disabled,false);
 // Run the actual boot sequence with a blocked fallback model and confirm history still loads.
 blocked=true;calls.length=0;
 let source=html.split('<script>')[1].split('</script>')[0];source=source.slice(source.lastIndexOf('(async()=>{try{const info='));
 vm.runInContext(source,context);
 for(let n=0;n<5;n++)await new Promise(setImmediate);
 assert.ok(calls.some(c=>c.url==='/api/messages'));
 assert.match(node('chat').textContent,/history preserved/);
 assert.equal(node('mode').disabled,false);
 preferenceMode='vision';calls.length=0;vm.runInContext(source,context);
 for(let n=0;n<5;n++)await new Promise(setImmediate);
 assert.equal(node('mode').value,'gemma');
 assert.ok(calls.some(c=>c.url==='/api/preferences'&&c.data.mode==='gemma'));
 console.log('27B UI checks passed: three-mode menu, persisted selection, reuse status, safe-switch error, and history loads after blocked autostart.');
})().catch(e=>{console.error(e);process.exitCode=1});
