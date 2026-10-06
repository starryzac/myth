import { useQuery } from '@tanstack/react-query';
import { getAccounts } from '../api/goals';
import { getDemoState } from '../api/demo';
import { errorMessage } from '../api/http';
import FutureIncomePlanningPanel from './FutureIncomePlanningPanel';

export default function FutureIncomePlanningHost({ mutationBlocked }: { mutationBlocked: boolean }) {
  const owner = useQuery({ queryKey: ['future-income-host-owner'], queryFn: getAccounts, retry: false, structuralSharing: false });
  const epoch = useQuery({ queryKey: ['future-income-host-open-epoch'], queryFn: getDemoState, retry: false, structuralSharing: false });
  return <section aria-label="条件收入当前用户与周期">
    <button type="button" disabled={owner.isFetching || epoch.isFetching} onClick={() => { void owner.refetch(); void epoch.refetch(); }}>只读刷新条件规划用户与周期</button>
    {(owner.isPending || epoch.isPending) && <p role="status">正在读取条件规划当前用户与周期…</p>}
    {(owner.isError || epoch.isError) && <p role="alert">{errorMessage(owner.error ?? epoch.error)}；当前条件规划来源未证明。</p>}
    {owner.data && epoch.data && !owner.isError && !epoch.isError && (epoch.data.available && epoch.data.epoch_id
      ? <FutureIncomePlanningPanel key={`${owner.data.user_id}:${epoch.data.epoch_id}`} userId={owner.data.user_id} epochId={epoch.data.epoch_id} mutationBlocked={mutationBlocked || owner.isFetching || epoch.isFetching} />
      : <p role="alert">当前开放周期未取得，不能登记新的条件假设。原请求仍可独立读取。</p>)}
  </section>;
}
