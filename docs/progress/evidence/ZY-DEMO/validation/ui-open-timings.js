async (page) => {
  const results=[];
  for (let i=0;i<6;i++) {
    const httpStart={value:0};
    const response=page.waitForResponse(r=>r.url().endsWith('/api/v1/zhiyu/state') && r.request().method()==='GET');
    const started=Date.now();
    if (i===0) await page.goto('http://127.0.0.1:19179/zhiyu.html');
    else await page.reload();
    const actualResponse=await response;
    const snapshot=await actualResponse.json();
    const httpFinished=Date.now();
    if (snapshot.environment_id!=='bf_test_9e4333a75fb649b2b39b440f67590090' || snapshot.epoch_id!=='077e5bab-69a9-4950-bbe5-5f5ef531a4c7' || snapshot.actions.length!==0 || snapshot.income_received || snapshot.goal) throw new Error('Initial round changed');
    await page.getByRole('heading',{name:'可自主使用上限',exact:true}).waitFor();
    await page.waitForFunction(()=>document.querySelectorAll('[role="status"]').length===0 && [...document.querySelectorAll('button')].some(b=>b.textContent==='刷新实际状态' && !b.disabled));
    if (await page.getByRole('alert').count()) throw new Error('Actual initial view has alert');
    results.push({sample:i,mode:i===0?'new-origin-first-navigation':'same-origin-reload',navigation_to_state_json_ms:httpFinished-started,usable_ms:Date.now()-started,http_status:actualResponse.status(),safe_idle_cents:snapshot.dashboard.boundary.safe_idle_cents,actions:snapshot.actions.length,income_received:snapshot.income_received,goal_present:!!snapshot.goal});
  }
  await page.screenshot({path:'output/playwright/zhiyu-20261006/READY-overview.png',fullPage:true});
  return {url:page.url(),definition:'Services already ready; first navigation to new origin in existing dedicated Edge, then five same-origin reloads; usable requires actual state, visible boundary heading, enabled refresh, no status or alert. Browser process launch is excluded.',results};
}
