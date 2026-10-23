// Real model calls and purchase execution: isolated demo database only. See docs/demo-purchase-production-flow.md.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const fs=require('node:fs'); const assert=require('node:assert/strict');
const base=process.env.TEST_UI_URL || 'http://127.0.0.1:13006';
if(!/^http:\/\/(127\.0\.0\.1|localhost):13006$/.test(base)) throw Error('Use the isolated capstone-demo-flow stack on port 13006.');
const output=process.env.TEST_REPORT || '/private/tmp/capstone-demo-flow-result.json';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}); const pages=[];
 const report=process.env.RESUME_REPORT ? JSON.parse(fs.readFileSync(process.env.RESUME_REPORT,'utf8')) : {};
 const errors=[];
 try {
  async function login(user){const p=await browser.newPage({viewport:{width:1600,height:1100}});pages.push(p);p.on('pageerror',e=>errors.push(e.message));p.setDefaultTimeout(30000);await p.goto(base);await p.getByLabel('아이디',{exact:true}).fill(user);await p.getByLabel('비밀번호',{exact:true}).fill('1234');await p.getByRole('button',{name:'로그인',exact:true}).click();await p.getByRole('heading',{name:'업무 현황',exact:true}).waitFor();return p;}
  async function api(p,path,method='GET',body){return p.evaluate(async({path,method,body})=>{const token=JSON.parse(sessionStorage.getItem('bom-session')).session.token;const r=await fetch('/api'+path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(path+' '+r.status+' '+await r.text());return r.json();},{path,method,body});}
  async function done(p,id){const until=Date.now()+300000;while(Date.now()<until){const t=await api(p,'/ai/tasks/'+id);if(t.status==='DONE')return t;if(t.status==='FAILED')throw Error(JSON.stringify(t));await new Promise(r=>setTimeout(r,700));}throw Error('task timeout '+id);}
  async function question(p,q){if(!await p.locator('.ai-panel').count())await p.getByRole('button',{name:'AI에 질문',exact:true}).click();const panel=p.locator('.ai-panel');await panel.getByLabel('선택한 업무에 질문').fill(q);const response=p.waitForResponse(r=>r.url().endsWith('/api/ai/tasks')&&r.request().method()==='POST');await panel.getByRole('button',{name:'분석 요청',exact:true}).click();const id=(await(await response).json()).taskId;console.log('task',id);return {id,task:await done(p,id)};}
  async function eventually(fn){let last;const until=Date.now()+180000;while(Date.now()<until){try {if(await fn())return;}catch(e){last=e;}await new Promise(r=>setTimeout(r,400));}throw last || Error('condition timed out');}
  const buyer=await login('buyer'), planner=await login('planner'), manager=await login('approver');
  if (!report.planner) {
  report.first=await question(buyer,'세미온반도체 공장 화재로 MCU 발주 PO-2609-008의 입고가 약 1주 늦어질 수 있다고 합니다. 릴레이 발주 PO-2610-106도 11월 27일로 늦어집니다. 두 지연을 함께 분석하고, MCU 지연이 장기화되면 어디부터 생산 차질이 생기는지도 보여 줘.');
  assert.equal(report.first.task.result.intent,'IMPACT'); assert.equal(report.first.task.result.whatif.length,2);assert.deepEqual(report.first.task.result.scenarios.map(s=>s.weeks),[1,4,12]);console.log('PASS first: multi-delay + 1/4/12 scenarios');
  await buyer.locator('.ai-panel').getByRole('button',{name:'새 대화',exact:true}).click();
  report.confirmed=await question(buyer,'협력사에서 입고일 변경을 확정 통보했습니다. MCU 발주 PO-2609-008은 2026년 11월 27일에서 12월 4일로, 릴레이 발주 PO-2610-106은 2026년 11월 20일에서 11월 27일로 늦어집니다. 현재 생산계획에 어떤 결품이 발생하나요?');
  assert.equal(report.confirmed.task.result.intent,'IMPACT');assert.equal(report.confirmed.task.result.receipt_delay.reported,true);
  await buyer.locator('.ai-panel').getByRole('button',{name:'생산팀에 일정 조정 요청',exact:true}).click();const drawer=buyer.getByRole('dialog',{name:'부서에 변경 요청'});
  await drawer.getByLabel('지연 사유',{exact:true}).fill('데모: 협력사 화재 및 납기 변경 확정 통보');await drawer.getByLabel('요청 내용',{exact:true}).fill('벽스위치 PP-03 600대의 생산일을 11/28로 조정 가능한지 검토해 주세요. 남은 MCU 부족은 구매팀에 연결해서 해결하겠습니다.');await drawer.getByRole('checkbox').check();
  const sent=buyer.waitForResponse(r=>r.url().endsWith('/api/coordination/requests')&&r.request().method()==='POST');await drawer.getByRole('button',{name:'지연 사실 반영 및 생산팀에 전송',exact:true}).click();report.parent=await(await sent).json();assert(report.parent.id,JSON.stringify(report.parent));console.log('PASS buyer handoff',report.parent.id);
  await planner.goto(base+'/requests?id='+report.parent.id);await planner.getByRole('button',{name:'내가 담당하기',exact:true}).click();await planner.getByRole('button',{name:'AI에게 생산 변경안 묻기',exact:true}).click();
  report.planner=await question(planner,'현재 저장된 입고 일정을 기준으로, 벽스위치 PP-03의 생산일을 2026년 11월 23일에서 11월 28일로 옮기면 어떤 문제가 남나요? 수량은 600대로 유지합니다. 릴레이 결품이 해소되는지, 다른 생산에 새 결품이 생기는지, MCU 부족은 어디에 남는지 확인해 주세요.');
  assert.equal(report.planner.task.result.intent,'IMPACT');
  assert.deepEqual(report.planner.task.result.whatif,[{op:'reschedule_plan',plan_id:'PP-03',new_date:'2026-11-28'}]);
  assert.equal(await planner.locator('.ai-panel').getByRole('button',{name:'구매팀에 해결 요청',exact:true}).count(),0);
  await planner.locator('.ai-panel').getByRole('link',{name:'이 생산계획 변경안 검토 →',exact:true}).click();
  }
  const plan=async()=> (await api(planner,'/plans')).plans.find(p=>p.plan_id==='PP-03');
  const purchases=async()=> (await api(buyer,'/purchase-orders')).purchase_orders;
  if (!report.completedParent) {
  await planner.goto(base+'/requests?id='+report.parent.id+'&analysisTaskId='+report.planner.id);
  const review=planner.locator('.plan-request-review');
  await eventually(async()=>await review.getByLabel('PP-03 변경 생산일',{exact:true}).inputValue()==='2026-11-28');
  const validated=planner.waitForResponse(r=>r.url().endsWith('/plan-preview')&&r.request().method()==='POST');
  await review.getByRole('button',{name:'변경안 검증',exact:true}).click();
  report.preview=await(await validated).json();
  assert.equal(report.preview.canExecute,true,JSON.stringify(report.preview));assert.equal(report.preview.partial,true);
  assert.deepEqual(report.preview.remainingImpact.shortages.map(x=>[x.item_id,x.plan_id,x.projected_stock]),[['MCU','PP-04',-500]]);
  report.purchasesBefore=await purchases();assert.equal((await plan()).plan_date,'2026-11-23');
  await review.getByLabel('변경 사유',{exact:true}).fill('확정된 릴레이 입고 이후인 11/28로 생산. 남는 MCU 부족은 구매팀에 요청합니다.');
  await review.getByRole('button',{name:'생산계획 먼저 저장',exact:true}).click();
  await planner.getByRole('dialog',{name:'생산계획을 먼저 저장할까요?'}).getByRole('button',{name:'돌아가기',exact:true}).click();
  assert.equal((await plan()).plan_date,'2026-11-23');
  await review.getByRole('button',{name:'생산계획 먼저 저장',exact:true}).click();
  await planner.getByRole('dialog',{name:'생산계획을 먼저 저장할까요?'}).getByRole('button',{name:'생산계획만 저장',exact:true}).click();
  await eventually(async()=> (await api(planner,'/coordination/requests/'+report.parent.id)).status==='PARTIALLY_COMPLETED');
  assert.equal((await plan()).plan_date,'2026-11-28');assert.deepEqual(await purchases(),report.purchasesBefore);
  await planner.reload();await planner.getByRole('heading',{name:'생산계획 저장 완료 · 구매 문제 남음',exact:true}).waitFor();
  await planner.screenshot({path:'/private/tmp/capstone-demo-step2-saved.png',fullPage:true});
  console.log('PASS step2: popup cancellation safe; production only saved, purchases unchanged, partial status survives reload');
  await planner.getByRole('button',{name:'남은 문제를 구매팀에 요청',exact:true}).click();
  const remaining=planner.getByRole('dialog',{name:'남은 문제를 구매팀에 요청'});
  await remaining.getByLabel('구매팀에 전달할 내용').fill('PP-03 생산을 11/28로 저장했습니다. 남은 11/30 PP-04 MCU 500개 부족만 기존 발주 당김 또는 분할로 해결해 주세요. 협력사 확약 여부와 다른 제품 영향도 확인하겠습니다.');
  const sent=planner.waitForResponse(r=>r.url().endsWith('/remaining-request')&&r.request().method()==='POST');
  await remaining.getByRole('button',{name:'구매팀에 남은 문제 전송',exact:true}).click();report.child=await(await sent).json();
  assert.equal(report.child.requestKind,'REMAINING');assert.deepEqual(report.child.planOps,[]);assert.deepEqual(report.child.analysis.shortages.map(x=>x.item_id),['MCU']);
  assert.equal((await api(planner,'/coordination/requests/'+report.parent.id)).status,'WAITING_LINKED');
  console.log('PASS handoff: only remaining MCU issue, no uncommitted plan operations',report.child.id);
  await buyer.goto(base+'/requests?id='+report.child.id);
  await buyer.getByRole('button',{name:'내가 담당하기',exact:true}).click();
  await buyer.getByRole('button',{name:'AI에게 해결 방법 묻기',exact:true}).click();
  if(!await buyer.locator('.ai-panel').count())await buyer.getByRole('button',{name:'AI에 질문',exact:true}).click();
  const panel=buyer.locator('.ai-panel');
  await panel.getByLabel('선택한 업무에 질문').fill('현재 저장된 생산계획은 유지하고, 이 요청의 남은 MCU 부족을 기존 발주 당김이나 분할로 해결해 줘. 세미온 계약 근거와 다른 제품에 미치는 영향도 보여 줘.');
  const solving=buyer.waitForResponse(r=>r.url().endsWith('/api/ai/tasks')&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'분석 요청',exact:true}).click();report.solver=(await(await solving).json()).taskId;
  await panel.locator('.task-review-item').first().waitFor({timeout:180000});
  const cards=(await api(buyer,'/approvals')).filter(x=>x.task_id===report.solver);report.cards=cards;
  assert(cards.length>0);assert(cards.every(x=>x.args.po_id));
  assert.deepEqual(await purchases(),report.purchasesBefore);assert.equal((await plan()).plan_date,'2026-11-28');
  await panel.getByLabel('전체 문제 설명 · 승인 요청 시 필수').fill('생산관리에서 PP-03 일정 변경 저장 완료. 남은 PP-04 MCU 500개 부족 해결 요청.');
  for(const memo of await panel.getByLabel('개인 메모',{exact:true}).all())await memo.fill('시연: 제안한 발주 당김/분할에 대해 협력사 가능 여부를 확인했다고 가정합니다.');
  await panel.getByRole('button',{name:'팀장에게 승인 요청',exact:true}).click();
  await eventually(async()=> (await api(manager,'/approvals')).some(x=>x.task_id===report.solver&&x.status==='PENDING'));
  await manager.goto(base+'/approvals');
  for(let i=0;i<cards.length;i++){
    await manager.getByRole('button',{name:'승인 상세',exact:true}).nth(i).click();
    const approval=manager.getByRole('dialog',{name:'발주 변경 승인 상세'});
    await approval.getByRole('button',{name:'승인',exact:true}).click();
    await manager.getByRole('dialog',{name:'이 제안의 검토를 완료할까요?'}).getByRole('button',{name:'검토 완료',exact:true}).click();
    await eventually(async()=> (await api(manager,'/ai/tasks/'+report.solver+'/batch')).entries.filter(e=>e.reviewStatus==='CONFIRMED').length===i+1);
    await approval.getByRole('button',{name:'상세 닫기',exact:true}).click();
  }
  assert.deepEqual(await purchases(),report.purchasesBefore);
  await manager.getByRole('button',{name:'승인 및 실행',exact:true}).click();
  const confirm=manager.getByRole('dialog',{name:'검토한 수정안을 함께 실행할까요?'});
  assert(!(await confirm.textContent()).includes('생산계획 함께 변경'));
  await confirm.getByRole('button',{name:'돌아가기',exact:true}).click();assert.deepEqual(await purchases(),report.purchasesBefore);
  await manager.getByRole('button',{name:'승인 및 실행',exact:true}).click();
  await manager.getByRole('dialog',{name:'검토한 수정안을 함께 실행할까요?'}).getByRole('button',{name:'전체 승인 및 실행',exact:true}).click();
  await eventually(async()=> (await api(planner,'/coordination/requests/'+report.parent.id)).status==='COMPLETED');
  report.completedParent=await api(planner,'/coordination/requests/'+report.parent.id);
  report.completedChild=await api(buyer,'/coordination/requests/'+report.child.id);
  report.batch=await api(manager,'/ai/tasks/'+report.solver+'/batch');
  assert.equal(report.completedChild.status,'COMPLETED');assert.equal((await plan()).plan_date,'2026-11-28');
  report.solverResult=await done(buyer,report.solver);
  assert.equal(report.solverResult.result.post_execution_impact.shortages.length,0);
  report.purchasesAfter=await purchases();assert.notDeepEqual(report.purchasesAfter,report.purchasesBefore);
  await planner.goto(base+'/requests?id='+report.parent.id);await planner.locator('.outcome').waitFor();
  await planner.screenshot({path:'/private/tmp/capstone-demo-step3-completed.png',fullPage:true});
  console.log('PASS step3: real-model proposal → buyer memo → manager review → atomic purchase execution; both requests completed');
  }
  report.solverResult=await done(buyer,report.solver);
  assert.equal(report.solverResult.result.post_execution_impact.shortages.length,0);
  report.purchasesAfter ??= await purchases();
  assert.notDeepEqual(report.purchasesAfter,report.purchasesBefore);
  await planner.goto(base+'/requests?id='+report.parent.id);await planner.locator('.outcome').waitFor();
  await planner.screenshot({path:'/private/tmp/capstone-demo-step3-completed.png',fullPage:true});
  console.log('PASS persisted step3: no shortages after execution, both requests completed');
  await buyer.goto(base+'/');await buyer.getByRole('button',{name:'AI에 질문',exact:true}).click();
  if(await buyer.locator('.ai-panel').getByRole('button',{name:'새 대화',exact:true}).count())await buyer.locator('.ai-panel').getByRole('button',{name:'새 대화',exact:true}).click();
  report.extended=await question(buyer,'현재 DB에 저장된 생산계획과 실행된 발주 변경을 기준으로 다시 분석해 줘. 세미온의 MCU 발주 PO-2609-008이 원래 예정일 2026년 11월 27일 기준 총 4주 지연되어, 새 입고일은 2026년 12월 25일이라고 합니다. 이 발주 입고일만 12월 25일로 가정하면 현재보다 어느 생산계획에서 MCU 결품이 새로 생기나요?');
  assert.equal(report.extended.task.result.intent,'IMPACT');
  assert.deepEqual(report.extended.task.result.whatif,[{op:'request_due_date_change',po_id:'PO-2609-008',new_due_date:'2026-12-25'}]);
  assert.equal((await plan()).plan_date,'2026-11-28');assert.deepEqual(await purchases(),report.purchasesAfter);
  console.log('PASS step4: current committed state + new absolute receipt date; analysis caused no business writes');
  assert.deepEqual(errors,[]);console.log('PASS no browser page errors');
 }finally{fs.writeFileSync(output,JSON.stringify(report,null,2));for(let i=0;i<pages.length;i++)await pages[i].screenshot({path:'/private/tmp/capstone-demo-final-'+i+'.png',fullPage:true}).catch(()=>{});await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
