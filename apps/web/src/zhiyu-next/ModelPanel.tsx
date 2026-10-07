import { useEffect, useState } from 'react';
import { getModelSettings, saveModelSettings, testModelConnection } from './api';
import type { ModelSettings } from './api';
import { userError } from './display';

export default function ModelPanel({ close }: { close: () => void }) {
  const [current, setCurrent] = useState<ModelSettings | null>(null);
  const [base, setBase] = useState(''); const [model, setModel] = useState(''); const [key, setKey] = useState('');
  const [enabled, setEnabled] = useState(false); const [timeout, setTimeout] = useState(30); const [limit, setLimit] = useState(3);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  useEffect(() => {
    let mounted = true;
    void getModelSettings().then((value) => { if (mounted) { setCurrent(value); setBase(value.base_url); setModel(value.model); setEnabled(value.enabled); setTimeout(value.timeout_seconds); setLimit(value.max_calls_per_turn); } }).catch((cause: unknown) => { if (mounted) setError(userError(cause)); });
    return () => { mounted = false; };
  }, []);
  async function save() {
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await saveModelSettings({ enabled, base_url: base.trim(), model: model.trim(), ...(key ? { api_key: key } : {}), timeout_seconds: timeout, max_calls_per_turn: limit });
      setCurrent(value); setNotice('模型配置已保存到扩展版私有后端。');
    } catch (cause) { setError(userError(cause)); }
    finally { setKey(''); setBusy(false); }
  }
  async function test() {
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await testModelConnection();
      setNotice(['PASSED', 'CONNECTED', 'SUCCEEDED', 'OK'].includes(value.status) ? '实际连接与协议检查通过。本次检查未产生资金动作。' : '连接检查未通过，请检查已保存的地址、模型和密钥。');
    } catch (cause) { setError(userError(cause)); }
    finally { setBusy(false); }
  }
  return <section className="zyn-model-panel zy-card" aria-label="模型设置">
    <div className="zy-section-heading"><h2>模型设置</h2><button className="zy-link" onClick={() => { setKey(''); close(); }}>关闭</button></div>
    <p className="zy-muted">模型帮助理解需求与解释结果。规则权限、金额和实际模拟执行由确定性服务核实。</p>
    {!current && !error && <p role="status">正在读取私有模型配置…</p>}
    <div className="zyn-form-grid"><label className="zy-field">服务地址<input type="url" value={base} placeholder="https://模型服务地址/v1" onChange={(event) => setBase(event.target.value)} autoComplete="off" /></label>
      <label className="zy-field">模型名称<input value={model} onChange={(event) => setModel(event.target.value)} autoComplete="off" /></label>
      <label className="zy-field">API Key<input type="password" value={key} placeholder={current?.api_key_configured ? '已配置；留空保留原密钥' : '仅保存到扩展版后端'} onChange={(event) => setKey(event.target.value)} autoComplete="new-password" /></label>
      <label className="zy-field">启用模型<select value={enabled ? 'on' : 'off'} onChange={(event) => setEnabled(event.target.value === 'on')}><option value="off">关闭</option><option value="on">启用</option></select></label>
      <label className="zy-field">超时（秒）<input type="number" min="1" max="120" value={timeout} onChange={(event) => setTimeout(Number(event.target.value))} /></label>
      <label className="zy-field">每轮最多调用<input type="number" min="1" max="6" value={limit} onChange={(event) => setLimit(Number(event.target.value))} /></label></div>
    <p className="zy-muted">{current?.api_key_configured ? `密钥：${current.api_key_mask || '已配置'}` : '密钥尚未配置'}。输入不会保存到浏览器记录，提交后自动清空。</p>
    {error && <p role="alert" className="zy-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="zy-buttons"><button className="zy-primary" disabled={busy || !current || enabled && (!base.trim() || !model.trim())} onClick={() => void save()}>保存设置</button><button className="zy-secondary" disabled={busy || !current?.configured} onClick={() => void test()}>测试已保存的连接</button></div>
  </section>;
}
