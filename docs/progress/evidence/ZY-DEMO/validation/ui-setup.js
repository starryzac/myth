async (page) => {
  const started = Date.now();
  const port = new URL(page.url()).port;
  const label = {19175:'C1',19176:'AB2',19177:'C2',19178:'AB3'}[port];
  if (!label) throw new Error('Unregistered acceptance URL');
  const records = [];
  const ready = () => page.waitForFunction(() => document.querySelectorAll('[role="status"]').length === 0);
  await ready();
  await page.getByRole('button',{name:'我的规则',exact:true}).click();
  for (const name of ['守住应急金 保留3000元应急金','为旅行储备 旅行目标1.2万元，截止2027-06-30，每月固定储备1000元。']) {
    await page.getByRole('button',{name,exact:true}).click();
    const response = page.waitForResponse(r=>r.url().endsWith('/api/v1/policies/compile') && r.request().method()==='POST');
    await page.getByRole('button',{name:'生成待确认候选',exact:true}).click();
    const r = await response; const candidate = await r.json();
    if (r.status()!==200 || !candidate.proposal_id) throw new Error('Actual candidate not confirmed');
    await ready();
    await page.getByRole('checkbox',{name:'我已复核金额、目标、有效期和允许的动作，明确确认此规则。',exact:true}).check();
    await page.getByRole('button',{name:'确认这条规则',exact:true}).click();
    await page.getByRole('region',{name:'待确认规则候选',exact:true}).waitFor({state:'hidden'});
    await ready();
    records.push({intent:name,proposal_id:candidate.proposal_id});
  }
  await page.getByRole('button',{name:'目标与执行',exact:true}).click();
  await page.getByRole('button',{name:'创建旅行目标',exact:true}).click();
  await page.getByRole('button',{name:'创建旅行目标',exact:true}).waitFor({state:'hidden'});
  await ready();
  if (await page.getByRole('progressbar',{name:'旅行目标进度'}).getAttribute('value')!=='0') throw new Error('Initial goal ownership is not zero');
  await page.getByRole('button',{name:'总览',exact:true}).click();
  const income = page.waitForResponse(r=>r.url().endsWith('/api/v1/zhiyu/income') && r.request().method()==='POST');
  await page.getByRole('button',{name:'注入固定收入事件',exact:true}).click();
  const ir = await income; const fact = await ir.json();
  if (ir.status()!==200 || fact.bank_status!=='SETTLED' || fact.projection_status!=='PROJECTED') throw new Error('Income not actually projected');
  await ready();
  await page.getByRole('button',{name:'目标与执行',exact:true}).click();
  await ready();
  await page.getByRole('combobox',{name:'场景',exact:true}).selectOption(label.startsWith('C')?'RESPONSE_LOSS':'SAFE');
  const prepare = page.waitForResponse(r=>r.url().endsWith('/api/v1/zhiyu/actions/prepare') && r.request().method()==='POST');
  const t=Date.now(); await page.getByRole('button',{name:'准备并查看计划',exact:true}).click();
  const pr=await prepare; const action=await pr.json();
  if (pr.status()!==200 || action.effect?.amount_cents!==100000 || action.autonomy_level!=='AUTO_EXECUTE') throw new Error('Unexpected original plan');
  const httpMs=Date.now()-t;
  await ready();
  await page.getByRole('checkbox',{name:'我已复核固定金额、来源、目标、费用和权限，接受此原操作。',exact:true}).waitFor();
  await page.screenshot({path:`output/playwright/zhiyu-20261006/${label}-prepared.png`,fullPage:true});
  return {label,setup_visible_ms:Date.now()-started,prepare_http_ms:httpMs,action_id:action.action_id,effect_hash:action.effect_hash,records};
}
