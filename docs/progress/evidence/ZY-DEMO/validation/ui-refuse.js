async (page) => {
  const label={19176:'AB2',19178:'AB3'}[new URL(page.url()).port];
  if (!label) throw new Error('Unregistered refusal URL');
  await page.getByRole('button',{name:'我的规则',exact:true}).click();
  const rule=page.getByRole('article').filter({has:page.getByRole('heading',{name:'旅行',exact:true})});
  await rule.getByRole('button',{name:'撤销规则',exact:true}).click();
  await page.getByRole('checkbox',{name:'我明确接受这次规则状态变更。',exact:true}).check();
  const t=Date.now(); await page.getByRole('button',{name:'确认变更',exact:true}).click();
  await page.getByRole('region',{name:'规则状态变更复核',exact:true}).waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.querySelectorAll('[role="status"]').length===0);
  await page.getByRole('button',{name:'目标与执行',exact:true}).click();
  await page.getByRole('combobox',{name:'场景',exact:true}).selectOption('REVOKED');
  const response=page.waitForResponse(r=>r.url().endsWith('/api/v1/zhiyu/actions/prepare') && r.request().method()==='POST');
  await page.getByRole('button',{name:'准备并查看计划',exact:true}).click();
  const r=await response; const rejection=await r.json();
  if (r.status()!==409 || rejection.error?.code!=='EXECUTION_NOT_READY') throw new Error('Actual revoke refusal missing');
  await page.waitForFunction(()=>document.querySelectorAll('[role="status"]').length===0);
  await page.getByRole('button',{name:'活动记录',exact:true}).click();
  await page.getByRole('heading',{name:'目标储备请求',exact:true}).waitFor();
  await page.screenshot({path:`output/playwright/zhiyu-20261006/${label}-refused.png`,fullPage:true});
  return {label,visible_ms:Date.now()-t,http:r.status(),error:rejection.error};
}
