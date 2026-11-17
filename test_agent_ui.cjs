const assert=require('node:assert/strict'),vm=require('node:vm');
const {context,node,all}=require('./test_chat_ui.cjs');
const trace={plan:['读取预算','核对金额'],status:'ready',steps:['计算：18*3 = 54'],trace:[{tool:'calculate',status:'completed',input:'18*3',summary:'计算：18*3 = 54'}]};
let chunks=[];
context.fetch=async(url,options)=>{
 if(url==='/api/sessions')return {ok:true,json:async()=>[{id:9,title:'预算'}]};
 if(url==='/api/messages')return {ok:true,json:async()=>[{role:'assistant',content:'结果是54。',image:'',sources:'[]',agent_trace:JSON.stringify(trace)}]};
 if(url==='/api/chat'){
  assert.equal(JSON.parse(options.body).agent,true);
  chunks=[{type:'agent_step',step:trace.trace[0]},{type:'result',answer:'结果是54。',sources:[],agent_trace:trace}];
  let i=0;return {ok:true,body:{getReader:()=>({async read(){
   if(i===1)assert.match(node('chat').textContent,/Agent 执行中.*已完成.*54/s);
   return i<chunks.length?{value:new TextEncoder().encode(JSON.stringify(chunks[i++])+'\n'),done:false}:{done:true};
  },releaseLock(){},async cancel(){}})}};
 }
 return {ok:true,json:async()=>({})};
};
(async()=>{
 vm.runInContext('session=9',context);node('agent').checked=true;node('input').value='每本18元买3本';await node('send').onclick();
 assert.match(node('chat').textContent,/Agent 执行记录/);
 assert.equal(all(node('chat')).filter(x=>x.className==='agent-panel').length,1,'Final trace replaces live trace');
 await vm.runInContext('load(9)',context);
 assert.match(node('chat').textContent,/计划（模型提出）.*读取预算/s);
 assert.match(node('chat').textContent,/已完成.*54/s);
 const bubble=node('chat').children[0];context.testBubble=bubble;
 vm.runInContext('renderAgentTrace(testBubble,{trace:[{status:"error",summary:"<script>bad()</script>"}],status:"limit"})',context);
 assert.equal(all(bubble).some(x=>x.tagName==='SCRIPT'),false);
 assert.match(bubble.textContent,/失败.*<script>/s);assert.match(bubble.textContent,/步数上限/);
 console.log('Agent UI checks passed: live progress, final replacement, persisted trace, untrusted text and limit labels.');
})().catch(e=>{console.error(e);process.exitCode=1});
