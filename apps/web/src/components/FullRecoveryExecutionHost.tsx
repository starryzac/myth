import { useQuery } from '@tanstack/react-query';
import { getAccounts } from '../api/goals';
import { getDemoState } from '../api/demo';
import { errorMessage } from '../api/http';
import type { FullPolicy } from '../api/full-policies';
import FullRecoveryExecutionPanel from './FullRecoveryExecutionPanel';
import FullRecoveryNextPanel from './FullRecoveryNextPanel';
import FullMaturityExecutionPanel from './FullMaturityExecutionPanel';

/** The original policy identity is usable only with an actual owner and open epoch. */
export default function FullRecoveryExecutionHost({ policy, mutationBlocked, maturityMutationBlocked = mutationBlocked }: { policy: FullPolicy; mutationBlocked: boolean; maturityMutationBlocked?: boolean }) {
  const owner = useQuery({ queryKey: ['full-recovery-host-owner'], queryFn: getAccounts, retry: false, structuralSharing: false });
  const epoch = useQuery({ queryKey: ['full-recovery-host-open-epoch'], queryFn: getDemoState, retry: false, structuralSharing: false });
  return <section aria-label="安全恢复当前来源">
    <button type="button" disabled={owner.isFetching || epoch.isFetching} onClick={() => { void owner.refetch(); void epoch.refetch(); }}>只读刷新恢复用户与周期</button>
    {(owner.isPending || epoch.isPending) && <p role="status">正在读取恢复当前用户与周期…</p>}
    {(owner.isError || epoch.isError) && <p role="alert">{errorMessage(owner.error ?? epoch.error)}；当前恢复来源未证明。</p>}
    {owner.data && epoch.data && !owner.isError && !epoch.isError && (epoch.data.available && epoch.data.epoch_id
      ? <><FullMaturityExecutionPanel policyId={policy.policy_id} userId={owner.data.user_id} expectedVersionId={policy.current_version.version_id} epochId={epoch.data.epoch_id} mutationBlocked={maturityMutationBlocked || owner.isFetching || epoch.isFetching} showOriginalRecovery={false} /><FullRecoveryNextPanel policyId={policy.policy_id} userId={owner.data.user_id} expectedVersionId={policy.current_version.version_id} epochId={epoch.data.epoch_id} mutationBlocked={mutationBlocked || owner.isFetching || epoch.isFetching} /><FullRecoveryExecutionPanel policyId={policy.policy_id} userId={owner.data.user_id} expectedVersionId={policy.current_version.version_id} epochId={epoch.data.epoch_id} mutationBlocked={mutationBlocked || owner.isFetching || epoch.isFetching} /></>
      : <p role="alert">当前开放周期未取得，尚不能准备新的安全恢复动作；独立原请求核对入口仍保留。</p>)}
  </section>;
}
