// Fresh isolated DB only: tests/compose.yml + compose.coordination-ui.yml, never dev port 3000.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');
const base = 'http://127.0.0.1:13004';
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const errors = [];
  try {
    async function login(user) {
      const p = await browser.newPage({viewport:{width:1500,height:1100}});
      p.setDefaultTimeout(45000); p.on('pageerror', e => errors.push(e.message));
      await p.goto(base); await p.getByLabel('아이디',{exact:true}).fill(user);
      await p.getByLabel('비밀번호',{exact:true}).fill('1234');
      await p.getByRole('button',{name:'로그인',exact:true}).click();
      await p.getByRole('heading',{name:'업무 현황',exact:true}).waitFor(); return p;
    }
    async function api(p,path) {
      return p.evaluate(async path => {
        const token=JSON.parse(sessionStorage.getItem('bom-session')).session.token;
        const r=await fetch('/api'+path,{headers:{Authorization:'Bearer '+token}});
        if(!r.ok) throw Error(path+' '+r.status); return r.json();
      },path);
    }
    const buyer=await login('buyer'), planner=await login('planner');
    const date=async()=> (await api(buyer,'/purchase-orders')).purchase_orders.find(p=>p.po_id==='PO-2609-002').due_date;
    assert.equal(await date(),'2026-11-13');
    await buyer.getByRole('button',{name:'AI에 질문',exact:true}).click();
    const panel=buyer.locator('.ai-panel');
    await panel.getByLabel('선택한 업무에 질문').fill('11/13 와이파이칩 입고일이 일주일 늦어졌어. 문제있나?');
    await panel.getByRole('button',{name:'분석 요청',exact:true}).click();
    await panel.getByRole('button',{name:'생산팀에 일정 조정 요청',exact:true}).click();
    let drawer=buyer.getByRole('dialog',{name:'부서에 변경 요청'});
    await drawer.getByText('분석한 입고 지연 · 아직 저장 전 · PO-2609-002',{exact:true}).waitFor();
    assert((await drawer.getByLabel('요청 내용',{exact:true}).inputValue()).includes('2026-11-13 → 2026-11-20'));
    const submit=()=>drawer.getByRole('button',{name:'지연 사실 반영 및 생산팀에 전송',exact:true});
    assert(await submit().isDisabled()); assert.equal(await date(),'2026-11-13');
    await drawer.getByRole('button',{name:'상세 닫기',exact:true}).click();
    assert.equal(await date(),'2026-11-13');
    await panel.getByRole('button',{name:'생산팀에 일정 조정 요청',exact:true}).click();
    drawer=buyer.getByRole('dialog',{name:'부서에 변경 요청'});
    await drawer.getByLabel('지연 사유',{exact:true}).fill('협력사 생산 지연 확정 통보');
    await drawer.getByRole('checkbox').check();
    const response=buyer.waitForResponse(r=>r.url().endsWith('/api/coordination/requests')&&r.request().method()==='POST');
    await submit().click();
    const r=await (await response).json(); assert(r.id,JSON.stringify(r));
    await buyer.waitForURL('**/requests?id='+r.id); assert.equal(await date(),'2026-11-20');
    await planner.goto(base+'/requests?id='+r.id);
    await planner.locator('.request-detail').getByText('협력사 생산 지연 확정 통보',{exact:true}).waitFor();
    await planner.getByRole('button',{name:'내가 담당하기',exact:true}).click();
    await planner.getByRole('button',{name:'AI에게 생산 변경안 묻기',exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log('PASS browser: buyer chat → auto-filled preview → cancel without writes → confirmed delay/share → planner receipt/claim');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
