import { useState } from 'react';
import { Client, type Dashboard, type GoalConfiguration, type GoalDetail } from './api';
import { yuanToCents, money } from './money';
import { userError } from './display';

interface PendingGoal { key: string; scope: string; goal_id?: string; body: GoalConfiguration }
function pendingGoal(scope: string): PendingGoal | null {
  const raw = localStorage.getItem(`zhiyu-go:goal-configuration:${scope}`);
  if (!raw) return null;
  const p = JSON.parse(raw) as PendingGoal;
  if (!p || p.scope !== scope || typeof p.key !== 'string' || p.key.length < 16 || !p.body || typeof p.body.name !== 'string' || typeof p.body.account_id !== 'string' || !/^[0-9]+$/.test(p.body.target_cents) || !Number.isSafeInteger(p.body.expected_version) || (p.goal_id != null && typeof p.goal_id !== 'string')) throw new Error('上次目标修改的定位资料需要核对，请保留浏览器资料');
  return p;
}

export function GoalManager({ client, scope, data, onChanged }: { client: Client; scope: string; data: Dashboard; onChanged: () => Promise<void> }) {
  const [name, setName] = useState('');
  const [target, setTarget] = useState('500.00');
  const [account, setAccount] = useState(data.accounts[0]?.id ?? '');
  const [editing, setEditing] = useState<GoalDetail | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState<PendingGoal | null>(() => { try { return pendingGoal(scope); } catch { return null; } });
  const storageKey = `zhiyu-go:goal-configuration:${scope}`;
  async function save() {
    if (busy) return;
    setError(''); setMessage(''); setBusy(true);
    try {
      // Re-read durable state before allocating a new key. A lost response or
      // reload keeps the original command and cannot duplicate the goal.
      let command = pendingGoal(scope);
      if (!command) {
        if (!consent) throw new Error('请先确认目标名称、金额和账户');
        command = { scope, key: crypto.randomUUID(), goal_id: editing?.id, body: { name: name.trim(), target_cents: yuanToCents(target), account_id: account, expected_version: editing?.version ?? 0 } };
        localStorage.setItem(storageKey, JSON.stringify(command));
        setPending(command);
      }
      const result = await client.configureGoal(command.key, command.body, command.goal_id);
      // A replay may intentionally return the original version; refresh obtains
      // the independently read current version and never changes the command.
      await onChanged();
      localStorage.removeItem(storageKey); setPending(null); setEditing(null); setName(''); setConsent(false);
      setMessage(`已保存“${result.name}”的目标设置，当前进度已重新读取。`);
    } catch (e) { setError(userError(e)); } finally { setBusy(false); }
  }
  return <section className="zy-card" aria-label="管理目标"><h2>{pending ? '核对上次目标设置' : editing ? `修改“${editing.name}”` : '添加一个目标'}</h2>
    {pending ? <p>上次设置的结果尚待核对。继续读取同一笔设置后，再开始新的修改。</p> : <>
      <p>最多管理 8 个目标。目标设置保存后，按新的事实重新生成计划。</p>
      <label className="zy-field">目标名称<input maxLength={80} value={name} disabled={busy} onChange={(e) => { setName(e.target.value); setConsent(false); }} /></label>
      <label className="zy-field">目标金额（元）<input inputMode="decimal" value={target} disabled={busy} onChange={(e) => { setTarget(e.target.value); setConsent(false); }} /></label>
      <label className="zy-field">归属账户<select value={account} disabled={busy || !!editing} onChange={(e) => { setAccount(e.target.value); setConsent(false); }}>{data.accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="zy-check"><input type="checkbox" checked={consent} disabled={busy} onChange={(e) => setConsent(e.target.checked)} />我确认保存以上目标设置。</label>
    </>}
    <button className="zy-primary" disabled={busy || (!pending && (!consent || !name.trim() || !account || (!editing && data.goals.length >= 8)))} onClick={() => { void save(); }}>{busy ? '正在核对…' : pending ? '继续核对上次设置' : '保存目标设置'}</button>
    {editing && !pending && <button className="zy-secondary" disabled={busy} onClick={() => { setEditing(null); setName(''); setConsent(false); }}>取消修改</button>}
    {!pending && <div className="zy-goal-edit-list">{data.goals.map((g) => <button className="zy-link" key={g.id} disabled={busy} onClick={() => { setBusy(true); setError(''); void client.goal(g.id).then((current) => { setEditing(current); setName(current.name); setAccount(current.account_id); setTarget(`${BigInt(current.target_cents) / 100n}.${(BigInt(current.target_cents) % 100n).toString().padStart(2, '0')}`); setConsent(false); }).catch((e) => setError(userError(e))).finally(() => setBusy(false)); }}>修改{g.name}（{money(g.target_cents)}）</button>)}</div>}
    {message && <p className="zy-message" role="status">{message}</p>}{error && <p className="zy-message zy-error" role="alert">{error}</p>}
  </section>;
}
