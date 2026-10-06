import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAccounts } from '../api/goals';
import { errorMessage } from '../api/http';
import { object } from '../features/policy-form';
import FinancialChangeImpactPanel from './FinancialChangeImpactPanel';
import FinancialChangeHistoryImpactPanel from './FinancialChangeHistoryImpactPanel';

/** Candidate financial read is separate from the original manual confirmation preview. */
export default function FullPolicyFinancialImpactHost({ policyId, expectedVersionId, expectedVersionNumber = 1, expectedEpochId, candidateText, blocked }: { policyId: string; expectedVersionId: string; expectedVersionNumber?: number; expectedEpochId?: string; candidateText: string; blocked: boolean }) {
  const [open, setOpen] = useState(false);
  const owner = useQuery({ queryKey: ['financial-change-preview-host-owner'], queryFn: getAccounts, enabled: open, retry: false });
  let configuration: Record<string, unknown> | null = null;
  try { const value: unknown = JSON.parse(candidateText); if (object(value)) configuration = value; } catch { /* Editable text remains unverified. */ }
  return <section aria-label="原编辑器资金差量入口">
    <button type="button" disabled={blocked} onClick={() => setOpen(!open)}>{open ? '收起候选资金差量预览' : '打开候选资金差量预览'}</button>
    {open && <><p>此处另行只读计算当前原件上的未来差量。原修改确认仍须原配置预览、hash复核、理由和明确勾选；资金预览本身不确认策略或赋执行权限。</p>
      {owner.isPending && <p role="status">正在读取资金预览当前用户…</p>}{owner.isError && <p role="alert">{errorMessage(owner.error)}；当前用户尚未读取。</p>}
      {!configuration && <p role="alert">候选JSON尚不是完整对象，未发送资金预览。</p>}
      {configuration && owner.data && !owner.isError && (expectedVersionNumber > 1
        ? <FinancialChangeHistoryImpactPanel policyId={policyId} userId={owner.data.user_id} expectedVersionId={expectedVersionId} expectedVersionNumber={expectedVersionNumber} expectedEpochId={expectedEpochId} candidateConfiguration={configuration} mutationBlocked={blocked || owner.isFetching} />
        : <FinancialChangeImpactPanel policyId={policyId} userId={owner.data.user_id} expectedVersionId={expectedVersionId} candidateConfiguration={configuration} mutationBlocked={blocked || owner.isFetching} />)}
    </>}
  </section>;
}
