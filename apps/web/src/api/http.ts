import type { components } from '../../../../packages/contracts/schema';
import { assertMoneyFields } from '../features/money';
import { object } from '../features/policy-form';

export class ApiError extends Error {
  constructor(message: string, public status: number, public code: string, public requestId: string | null) { super(message); }
}
export async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? ''}/api/v1${path}`, {
      method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
  } catch { throw new ApiError('连接中断，请保留当前内容并重试同一请求', 0, 'NETWORK_ERROR', null); }
  let value: unknown;
  try { value = await response.json(); } catch { throw new ApiError('服务响应未通过完整性校验', response.status, 'INVALID_RESPONSE', null); }
  if (!response.ok) {
    const envelope = value as Partial<components['schemas']['ErrorEnvelope']>;
    const error = object(envelope?.error) ? envelope.error : null;
    throw new ApiError(typeof error?.message === 'string' ? error.message : '请求未完成，请核对配置后重试', response.status,
      typeof error?.code === 'string' ? error.code : 'REQUEST_FAILED', typeof error?.request_id === 'string' ? error.request_id : null);
  }
  assertMoneyFields(value);
  if (!object(value) || value.simulation !== true) throw new ApiError('响应未通过模拟环境校验', response.status, 'INVALID_RESPONSE', null);
  return value as T;
}
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? `${error.message}${error.requestId ? ` · 请求编号 ${error.requestId}` : ''}` : error instanceof Error ? error.message : '读取失败';
}
