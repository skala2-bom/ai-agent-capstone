// Run only against the isolated tests/compose.yml + compose.coordination-ui.yml stack.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');
const base = process.env.TEST_UI_URL || 'http://127.0.0.1:13004';
if (!/^http:\/\/(127\.0\.0\.1|localhost):13004$/.test(base)) throw Error('Use the isolated coordination UI at localhost:13004.');
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  const errors = [], pages = [];
  try {
    async function login(user) {
      const page = await browser.newPage({viewport:{width:1500,height:1100}}); pages.push(page);
      page.setDefaultTimeout(25000); page.on('pageerror', e => errors.push(e.message));
      await page.goto(base); await page.getByLabel('아이디',{exact:true}).fill(user);
      await page.getByLabel('비밀번호',{exact:true}).fill('1234');
      await page.getByRole('button',{name:'로그인',exact:true}).click();
      await page.getByRole('heading',{name:'업무 현황',exact:true}).waitFor(); return page;
    }
    async function api(page,path,method='GET',body) {
      return page.evaluate(async ({path,method,body}) => {
        const token=JSON.parse(sessionStorage.getItem('bom-session')).session.token;
        const r=await fetch('/api'+path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
        if(!r.ok) throw Error(path+' '+r.status+' '+await r.text()); return r.json();
      },{path,method,body});
    }
    async function eventually(fn) {
      let last; const until=Date.now()+90000;
      while(Date.now()<until) { try { const result=await fn(); if(result) return result; } catch(e) {last=e;} await new Promise(r=>setTimeout(r,300)); }
      throw last || Error('condition timed out');
    }
    async function question(page,text) {
      if(!await page.locator('.ai-panel').count()) await page.getByRole('button',{name:'AI에 질문',exact:true}).click();
      const panel=page.locator('.ai-panel');
      await panel.getByLabel('선택한 업무에 질문').fill(text);
      const response=page.waitForResponse(r=>r.url().endsWith('/api/ai/tasks')&&r.request().method()==='POST');
      await panel.getByRole('button',{name:'분석 요청',exact:true}).click();
      return (await (await response).json()).taskId;
    }
    async function sendFromPanel(page,message,button) {
      await page.locator('.ai-panel').getByRole('button',{name:button,exact:true}).click();
      const drawer=page.getByRole('dialog',{name:'부서에 변경 요청'});
      await drawer.getByLabel('요청 내용',{exact:true}).fill(message);
      await drawer.getByRole('checkbox').check();
      const response=page.waitForResponse(r=>r.url().endsWith('/api/coordination/requests')&&r.request().method()==='POST');
      await drawer.getByRole('button',{name:'동의하고 요청 보내기',exact:true}).click();
      const item=await (await response).json(); assert(item.id,JSON.stringify(item));
      await page.waitForURL('**/requests?id='+item.id); return item;
    }
    async function requestPage(page,id) { await page.goto(base+'/requests?id='+id); await page.locator('.request-detail').waitFor(); }
    async function sourceApi(page,text) {
      const {taskId}=await api(page,'/ai/tasks','POST',{question:text});
      await eventually(async()=> (await api(page,'/ai/tasks/'+taskId)).status==='DONE'); return taskId;
    }
    async function sendApi(page,task,message) {
      const preview=await api(page,'/coordination/preview?taskId='+task);
      return api(page,'/coordination/requests','POST',{sourceTaskId:task,expectedFingerprint:preview.fingerprint,commandId:crypto.randomUUID(),message});
    }
    const planner=await login('planner'), buyer=await login('buyer'), manager=await login('approver');
    await question(planner,'허브 생산을 11/25로 당기면 문제 있어?');
    const forward=await sendFromPanel(planner,'브라우저 검증: 허브 생산 앞당김','구매팀에 해결 요청');
    await requestPage(buyer,forward.id);
    await buyer.getByRole('button',{name:'내가 담당하기',exact:true}).click();
    await buyer.getByRole('button',{name:'AI에게 해결 방법 묻기',exact:true}).click();
    const solver=await question(buyer,'이 요청 해결방법 알려줘');
    const panel=buyer.locator('.ai-panel');
    await panel.locator('.task-review-item').waitFor({timeout:90000});
    await panel.getByLabel('전체 문제 설명 · 승인 요청 시 필수').fill('생산팀의 허브 생산 일정 변경 요청');
    await panel.getByLabel('개인 메모',{exact:true}).fill('협력사 분할 납품 가능 확인');
    await panel.getByRole('button',{name:'팀장에게 승인 요청',exact:true}).click();
    await eventually(async()=> (await api(manager,'/approvals')).some(x=>x.task_id===solver&&x.status==='PENDING'));
    await manager.goto(base+'/approvals');
    await manager.getByRole('button',{name:'승인 상세',exact:true}).click();
    const approval=manager.getByRole('dialog',{name:'발주 변경 승인 상세'});
    await approval.getByRole('button',{name:'승인',exact:true}).click();
    await manager.getByRole('dialog',{name:'이 제안의 검토를 완료할까요?'}).getByRole('button',{name:'검토 완료',exact:true}).click();
    await eventually(async()=> (await api(manager,'/ai/tasks/'+solver+'/batch')).entries[0].reviewStatus==='CONFIRMED');
    assert.equal((await api(planner,'/plans')).plans.find(p=>p.plan_id==='PP-05').plan_date,'2026-12-14');
    await approval.getByRole('button',{name:'상세 닫기',exact:true}).click();
    await manager.getByRole('button',{name:'승인 및 실행',exact:true}).click();
    const execution=manager.getByRole('dialog',{name:'검토한 수정안을 함께 실행할까요?'});
    assert((await execution.textContent()).includes('생산계획 함께 변경'));
    await execution.getByRole('button',{name:'돌아가기',exact:true}).click();
    assert.equal((await api(planner,'/plans')).plans.find(p=>p.plan_id==='PP-05').plan_date,'2026-12-14');
    await manager.getByRole('button',{name:'승인 및 실행',exact:true}).click();
    await manager.getByRole('dialog').getByRole('button',{name:'전체 승인 및 실행',exact:true}).click();
    await eventually(async()=> (await api(planner,'/coordination/requests/'+forward.id)).status==='COMPLETED');
    assert.equal((await api(planner,'/plans')).plans.find(p=>p.plan_id==='PP-05').plan_date,'2026-11-25');
    console.log('PASS browser: planner sharing → buyer chat → manager review → atomic execution, cancel popup is safe');

    const source=await sourceApi(planner,'11/23 스위치 생산을 900대로 늘리면 문제 있어?');
    const denied=await sendApi(planner,source,'브라우저 검증: 최종 거절 사유 전달');
    await requestPage(buyer,denied.id); await buyer.getByRole('button',{name:'내가 담당하기',exact:true}).click();
    await buyer.getByRole('button',{name:'팀장에게 요청 검토 전달',exact:true}).click();
    await requestPage(manager,denied.id); await manager.getByRole('button',{name:'요청 최종 거절',exact:true}).click();
    await manager.getByLabel('요청 부서에 전달할 최종 거절 사유').fill('발주 변경 불가, 생산 일정 재협의 필요');
    await manager.getByRole('button',{name:'최종 거절 내용 확인',exact:true}).click();
    await manager.getByRole('dialog').getByRole('button',{name:'최종 거절 및 사유 전달',exact:true}).click();
    await requestPage(planner,denied.id);
    await planner.locator('.outcome').filter({hasText:'발주 변경 불가, 생산 일정 재협의 필요'}).waitFor();
    await planner.reload(); await planner.locator('.outcome').filter({hasText:'최종 거절'}).waitFor();
    console.log('PASS browser: request-level rejection without proposals, reason survives reload');

    await buyer.goto(base+'/purchasing');
    await buyer.getByRole('row').filter({hasText:'PO-2610-106'}).getByRole('button',{name:'상세 보기',exact:true}).click();
    const purchase=buyer.getByRole('dialog');
    await purchase.getByLabel('변경 구분').selectOption('delay');
    await purchase.getByLabel('변경 입고 예정일').fill('2026-11-27');
    await purchase.getByLabel('변경 사유',{exact:true}).fill('협력사에서 지연 확정 통보');
    await purchase.getByRole('button',{name:'지연 사실 저장 및 영향 분석',exact:true}).click();
    const reverse=await sendFromPanel(buyer,'브라우저 검증: 릴레이 지연에 따른 생산일 조정','생산팀에 일정 조정 요청');
    await requestPage(planner,reverse.id); await planner.getByRole('button',{name:'내가 담당하기',exact:true}).click();
    await planner.getByRole('button',{name:'AI에게 생산 변경안 묻기',exact:true}).click();
    await question(planner,'스위치 11/23 생산을 11/28로 미루면 문제 있어?');
    await planner.locator('.ai-panel').getByRole('link',{name:'이 생산계획 변경안 검토 →',exact:true}).click();
    const plan=planner.locator('.plan-request-review');
    await eventually(async()=> await plan.getByLabel('PP-03 변경 생산일',{exact:true}).inputValue()==='2026-11-28');
    await plan.getByRole('button',{name:'변경안 검증',exact:true}).click();
    await plan.getByLabel('변경 사유',{exact:true}).fill('자재 도착 후 생산으로 확정');
    await plan.getByRole('button',{name:'생산계획 변경 확정',exact:true}).click();
    await planner.getByRole('dialog').getByRole('button',{name:'돌아가기',exact:true}).click();
    assert.equal((await api(planner,'/plans')).plans.find(p=>p.plan_id==='PP-03').plan_date,'2026-11-23');
    await plan.getByRole('button',{name:'생산계획 변경 확정',exact:true}).click();
    await planner.getByRole('dialog').getByRole('button',{name:'생산계획 변경 확정',exact:true}).click();
    await eventually(async()=> (await api(buyer,'/coordination/requests/'+reverse.id)).status==='COMPLETED');
    await requestPage(buyer,reverse.id); await buyer.locator('.outcome').filter({hasText:'처리 완료'}).waitFor();
    await buyer.screenshot({path:'/private/tmp/capstone-coordination-completed.png',fullPage:true});
    console.log('PASS browser: recorded delay → planner AI analysis → staged plan preview → confirmation → buyer notification');
    assert.deepEqual(errors,[]); console.log('PASS browser: no page errors');
  } catch(e) {
    for(let i=0;i<pages.length;i++) await pages[i].screenshot({path:`/private/tmp/capstone-coordination-failure-${i}.png`,fullPage:true}).catch(()=>{});
    throw e;
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
