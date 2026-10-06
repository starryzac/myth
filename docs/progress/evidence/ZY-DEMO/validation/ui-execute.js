async (page) => {
  const port=new URL(page.url()).port;
  const label={19175:'C1',19176:'AB2',19177:'C2',19178:'AB3'}[port];
  if (!label) throw new Error('Unregistered acceptance URL');
  await page.getByRole('checkbox',{name:'我已复核固定金额、来源、目标、费用和权限，接受此原操作。',exact:true}).check();
  const response=page.waitForResponse(r=>r.url().includes('/api/v1/zhiyu/actions/') && r.url().endsWith('/execute') && r.request().method()==='POST');
  const t=Date.now(); await page.getByRole('button',{name:'执行这个原操作',exact:true}).click();
  const r=await response; const action=await r.json(); const httpMs=Date.now()-t;
  await page.waitForFunction(()=>document.querySelectorAll('[role="status"]').length===0);
  if (r.status()!==200) throw new Error('Execution did not return actual state');
  if (label.startsWith('C')) {
    if (action.status!=='UNKNOWN' || action.receipt!==null || action.bank_status!=='SETTLED') throw new Error('Injected loss did not preserve UNKNOWN');
    await page.getByRole('region',{name:'待核实原操作',exact:true}).waitFor();
  } else {
    if (action.status!=='SUCCEEDED' || action.receipt?.executed_cents!==100000) throw new Error('Missing actual success receipt');
    await page.getByText('实际执行 ¥1,000.00，回执已保存。目标进度与边界已重新读取。',{exact:true}).waitFor();
  }
  await page.screenshot({path:`output/playwright/zhiyu-20261006/${label}-${label.startsWith('C')?'unknown':'success'}.png`,fullPage:true});
  return {label,http_ms:httpMs,visible_ms:Date.now()-t,action_id:action.action_id,effect_hash:action.effect_hash,status:action.status,bank_status:action.bank_status,receipt:action.receipt};
}
