import { expect, test } from '@playwright/test';
import type { Page, Request, Response, TestInfo } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { components } from '../../../../packages/contracts/schema';

type State = components['schemas']['DemoState'];
type Command = components['schemas']['DemoCommandView'];
type Action = components['schemas']['ActionResponse'];
type Kind = components['schemas']['DemoEventRequest']['event_kind'];
type Template = components['schemas']['DemoTemplateView']['kind'];
type Mode = 'BEGIN' | 'READ_ONLY' | 'MONEY' | 'RESET';
type LegacyRecoveryRead = {
  action_id: string; bank_request_id: string; bank_status: string; receipt_id: string; verified: boolean; scope: string;
  receipt: { id: string; action_plan_id: string; status: string; executed_cents: number; fee_cents: number; loss_cents: number; occurred_at: string; reconciled_at: string; response: { bank_request_id: string; posting_ids: string[] } };
};
type GoalIncomeRead = { goal_id: string; request: { kind: string; amount_cents: number; account_id: string; counterparty_ref: string; occurred_at: string; external_ref: string }; result: components['schemas']['ExternalFactResult']; scope: string };
const enabled = process.env.BF_W1_BROWSER_RUN === '1';
const runDirectory = process.env.BF_W1_RUN_DIRECTORY;
const titles: Record<Kind, string> = { SALARY_RECEIVED: '工资到账', CREATE_CAR_GOAL: '新建买车目标', LARGE_CONSUMPTION: '用户大额消费', AUTO_REDEEM: '自动赎回', FIXED_EARLY_WITHDRAWAL: '定存提前支取需确认', CHANGE_RENT: '修改房租策略' };

/** The coordinator admits resource identities for fixed simulator inputs and read-only assertions; never financial results. */
async function broker(scenario: string, operation: string, values: Record<string, unknown> = {}) {
  if (!enabled || !runDirectory) throw new Error('Guarded W1 coordinator required');
  const id = randomUUID();
  const folder = join(runDirectory, 'broker');
  const pending = join(folder, `${id}.pending`);
  await writeFile(pending, JSON.stringify({ protocol: 'bounded-funds-scenario-v1', id, scenario_id: scenario, operation, ...values }), { flag: 'wx' });
  await rename(pending, join(folder, `${id}.request.json`));
  const reply = join(folder, `${id}.response.json`);
  await expect.poll(() => existsSync(reply), { timeout: 1_200_000, intervals: [100, 250, 500] }).toBe(true);
  const result = JSON.parse(await readFile(reply, 'utf8')) as { id: string; status: string; result: Record<string, unknown>; error?: string };
  expect(result.id).toBe(id);
  expect(result.status, result.error ?? 'Scenario coordinator failed').toBe('PASSED');
  return result.result;
}

class RealUI {
  private serial = 0;
  private captureSequence = 0;
  private active = new Set<Request>();
  private saves = new Set<Promise<void>>();
  private latestState: State | undefined;
  private stateSequence = 0;
  private failure: Error | undefined;
  readonly folder: string;
  constructor(readonly page: Page, readonly info: TestInfo, readonly scenario: string) {
    this.folder = info.outputPath('native-http');
    this.page.setDefaultTimeout(30_000);
    this.page.setDefaultNavigationTimeout(120_000);
  }
  async start() {
    await mkdir(this.folder, { recursive: true });
    this.page.on('request', (request) => { if (request.url().includes('/api/v1/')) this.active.add(request); });
    this.page.on('requestfinished', (request) => this.active.delete(request));
    this.page.on('requestfailed', (request) => { this.active.delete(request); });
    this.page.on('response', (response) => {
      if (!response.url().includes('/api/v1/')) return;
      const saved = this.saveResponse(response).catch((error: unknown) => { this.failure = error instanceof Error ? error : new Error(String(error)); });
      this.saves.add(saved); void saved.finally(() => this.saves.delete(saved));
    });
    await this.page.goto('/#demo');
    await expect(this.page.getByRole('heading', { name: '演示控制台', exact: true })).toBeVisible();
    await this.settle();
    await writeFile(this.info.outputPath('actual-browser.json'), JSON.stringify({ requested_channel: 'msedge', actual_engine_version: this.page.context().browser()?.version() ?? null, actual_user_agent: await this.page.evaluate(() => navigator.userAgent), run_id: runDirectory?.split(/[\\/]/).at(-1), scenario_id: this.scenario }, null, 2), { flag: 'wx' });
    await expect.poll(() => this.latestState?.epoch_id, { timeout: 600_000 }).toBeTruthy();
  }
  private async saveResponse(response: Response) {
    const index = ++this.serial; const request = response.request();
    const body = await response.body();
    await writeFile(join(this.folder, `${index}.response.body`), body, { flag: 'wx' });
    await writeFile(join(this.folder, `${index}.request-response.json`), JSON.stringify({ method: request.method(), url: response.url(), request_body: request.postData(), status: response.status(), request_id: response.headers()['x-request-id'] ?? null, content_type: response.headers()['content-type'] ?? null, body_file: `${index}.response.body`, finished_at: new Date().toISOString() }, null, 2), { flag: 'wx' });
    if (response.status() >= 500) throw new Error(`Actual HTTP ${response.status()} ${response.url()}`);
    if (request.method() === 'GET' && new URL(response.url()).pathname === '/api/v1/demo/state' && response.ok()) {
      this.latestState = JSON.parse(body.toString('utf8')) as State; this.stateSequence = index;
    }
  }
  async settle() {
    await expect.poll(() => this.active.size, { timeout: 600_000, intervals: [100, 250, 500] }).toBe(0);
    while (this.saves.size) await Promise.all([...this.saves]);
    if (this.failure) throw this.failure;
  }
  state(): State {
    if (!this.latestState || !this.latestState.epoch_id) throw new Error('No actual native demo state');
    return this.latestState;
  }
  async get<T>(path: string): Promise<T> {
    // This helper is deliberately GET-only. Business writes use actual page controls.
    const response = await this.page.request.get(`/api/v1${path}`, { timeout: 600_000 });
    const body = await response.body(); const index = ++this.serial;
    await writeFile(join(this.folder, `${index}.assertion-get.body`), body, { flag: 'wx' });
    await writeFile(join(this.folder, `${index}.assertion-get.json`), JSON.stringify({ method: 'GET', path, status: response.status(), request_id: response.headers()['x-request-id'] ?? null }, null, 2), { flag: 'wx' });
    expect(response.ok(), body.toString('utf8').slice(0, 1000)).toBe(true);
    return JSON.parse(body.toString('utf8')) as T;
  }
  async screenshot(label: string) {
    await this.settle(); const capture = `${String(++this.captureSequence).padStart(4, '0')}-${label}`;
    await writeFile(this.info.outputPath(`${capture}.native.txt`), await this.page.locator('body').innerText(), { flag: 'wx' });
    await this.page.screenshot({ path: this.info.outputPath(`${capture}.native.png`), fullPage: true });
  }
  async checkpoint(label: string, mode: Mode = 'MONEY') {
    await this.settle(); await broker(this.scenario, 'checkpoint', { label, mode, expected_epoch_id: this.state().epoch_id });
  }
  async reset(label: string) {
    await this.page.goto('/#demo'); await this.settle();
    await this.checkpoint(`${label}-before`, 'BEGIN'); const original = this.state().epoch_id; const sequence = this.stateSequence;
    await this.page.getByRole('button', { name: '恢复演示初始状态', exact: true }).click();
    const review = this.page.getByRole('region', { name: '重置明确确认', exact: true });
    const confirm = review.getByRole('button', { name: '明确确认重置', exact: true });
    await expect(confirm).toBeDisabled(); await review.getByRole('checkbox').check();
    const actualReset = this.page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/demo/reset', { timeout: 600_000 });
    await confirm.click(); const resetResponse = await actualReset;
    expect(resetResponse.ok(), 'Actual reset HTTP must succeed before awaiting a new epoch').toBe(true);
    await expect.poll(() => this.stateSequence, { timeout: 600_000 }).toBeGreaterThan(sequence); await this.settle();
    expect(this.state().epoch_id).not.toBe(original);
    expect(this.state().templates.every((item) => item.confirmed_policy_id == null && item.proposal_id == null)).toBe(true);
    expect(this.state().commands).toHaveLength(0); await this.checkpoint(`${label}-after`, 'RESET');
  }
  async template(kind: Template) {
    const original = this.state().templates.find((item) => item.kind === kind)!;
    let sequence = this.stateSequence;
    await this.page.getByRole('button', { name: `生成候选：${original.title}`, exact: true }).click();
    await expect.poll(() => this.stateSequence, { timeout: 600_000 }).toBeGreaterThan(sequence); await this.settle();
    const proposed = this.state().templates.find((item) => item.kind === kind)!;
    expect(proposed.proposal_id).toBeTruthy(); expect(proposed.confirmed_policy_id).toBeNull();
    const card = this.page.getByRole('article', { name: `演示模板 ${original.title}`, exact: true });
    const confirm = card.getByRole('button', { name: `确认模板：${original.title}`, exact: true });
    await expect(confirm).toBeDisabled(); sequence = this.stateSequence;
    await card.getByRole('checkbox').check(); await confirm.click();
    await expect.poll(() => this.stateSequence, { timeout: 600_000 }).toBeGreaterThan(sequence); await this.settle();
    const result = this.state().templates.find((item) => item.kind === kind)!;
    expect(result.confirmed_policy_id).toBeTruthy(); expect(result.configuration_hash).toBe(proposed.configuration_hash);
    return result;
  }
  async event(kind: Kind, expected: Command['status'] = 'COMPLETED') {
    const previous = this.state().commands.find((item) => item.event_kind === kind); const sequence = this.stateSequence;
    await this.page.getByRole('button', { name: `${previous ? '恢复原事件：' : '注入事件：'}${titles[kind]}`, exact: true }).click();
    await expect.poll(() => this.stateSequence, { timeout: 600_000 }).toBeGreaterThan(sequence); await this.settle();
    const command = this.state().commands.find((item) => item.event_kind === kind)!;
    expect(command).toBeTruthy(); expect(command.epoch_id).toBe(this.state().epoch_id); expect(command.status, command.message).toBe(expected);
    if (previous) expect(command.command_id).toBe(previous.command_id);
    await expect(this.page.getByRole('region', { name: `原事件结果 ${kind}`, exact: true })).toContainText(command.command_id);
    return command;
  }
  async core() {
    await this.template('CAR_GOAL'); await this.template('LIQUID_ASSET');
    const created = await this.event('CREATE_CAR_GOAL');
    const goals = await this.get<components['schemas']['GoalList']>('/goals');
    const goal = goals.items.find((item) => item.id === created.goal_id)!;
    expect(goal).toBeTruthy(); expect(goal.allocated_cents).toBe(0);
    const salary = await this.event('SALARY_RECEIVED');
    expect(salary.fact?.bank_status).toBe('SETTLED'); expect(salary.fact?.projection_status).toBe('PROJECTED');
    for (const kind of ['ALLOCATE_GOAL', 'PURCHASE_ASSET']) {
      const action = (salary.actions ?? []).find((item) => item.effect.action_type === kind)!;
      expect(action, `Missing actual salary ${kind}`).toBeTruthy(); this.receipt(action);
    }
    await this.checkpoint('salary-real-legs'); return salary;
  }
  receipt(action: Action) {
    expect(action.status).toBe('SUCCEEDED'); expect(action.receipt?.status).toBe('SUCCEEDED');
    expect(action.receipt?.action_id).toBe(action.action_id); expect(action.receipt?.bank_operation_id).toBeTruthy();
    expect(action.receipt?.posting_ids.length).toBeGreaterThan(0); expect(action.receipt?.executed_cents).toBeGreaterThan(0);
  }
  async recovery() {
    const consumption = await this.event('LARGE_CONSUMPTION');
    expect(consumption.fact?.bank_status).toBe('SETTLED'); expect(consumption.fact?.projection_status).toBe('PROJECTED');
    const recovered = await this.event('AUTO_REDEEM');
    expect(recovered.recovery?.status).toBe('RECOVERED'); expect(recovered.recovery?.actual_boundary.status).toBe('READY');
    expect(recovered.recovery?.actions.length).toBeGreaterThan(0);
    await this.checkpoint('consumption-real-recovery');
    const original = await this.get<components['schemas']['RecoveryRunResponse']>(`/recovery/runs/${recovered.recovery!.run_id}`);
    expect(original.run_id).toBe(recovered.recovery!.run_id); expect(original.status).toBe('RECOVERED');
    expect(original.actions).toEqual(recovered.recovery!.actions); expect(original.plan.plan_hash).toBe(recovered.recovery!.plan.plan_hash);
    for (const item of original.actions) {
      expect(item.status).toBe('SUCCEEDED'); expect(item.bank_status).toBe('SETTLED');
      expect(item.bank_request_id).toBeTruthy(); expect(item.receipt_id).toBeTruthy();
      const step = original.plan.steps?.find((candidate) => candidate.position_id === item.position_id);
      expect(step).toBeTruthy(); expect(step!.autonomy_level).toBe('AUTO_EXECUTE');
      expect(step!.quote.loss_cents).toBe(0); expect(step!.quote.fee_cents).toBe(0);
      // Legacy recovery retains its original bank_request and receipt protocol.
      const proof = await broker(this.scenario, 'verify_legacy_recovery', { expected_epoch_id: this.state().epoch_id, action_id: item.action_id }) as unknown as LegacyRecoveryRead;
      expect(proof.verified).toBe(true); expect(proof.scope).toBeTruthy();
      expect(proof.action_id).toBe(item.action_id); expect(proof.bank_request_id).toBe(item.bank_request_id);
      expect(proof.bank_status).toBe('SETTLED'); expect(proof.receipt_id).toBe(item.receipt_id);
      expect(proof.receipt.id).toBe(item.receipt_id); expect(proof.receipt.action_plan_id).toBe(item.action_id);
      expect(proof.receipt.status).toBe('SUCCEEDED'); expect(Number.isSafeInteger(proof.receipt.executed_cents)).toBe(true);
      expect(proof.receipt.executed_cents).toBe(step!.quote.principal_cents); expect(proof.receipt.executed_cents).toBeGreaterThan(0);
      expect(proof.receipt.loss_cents).toBe(0); expect(proof.receipt.fee_cents).toBe(0);
      expect(proof.receipt.response.bank_request_id).toBe(item.bank_request_id); expect(proof.receipt.response.posting_ids).toHaveLength(2);
      expect(proof.receipt.occurred_at).toBeTruthy(); expect(proof.receipt.reconciled_at).toBeTruthy();
    }
    await this.checkpoint('legacy-recovery-read-zero-write', 'READ_ONLY'); return recovered;
  }
  async fixed() {
    await this.template('FIXED_ASSET'); let command = await this.event('FIXED_EARLY_WITHDRAWAL', 'WAITING_ACTION_CONFIRMATION');
    const confirmed = new Map<string, string>();
    for (let stage = 0; command.status === 'WAITING_ACTION_CONFIRMATION' && stage < 2; stage++) {
      const action = (command.actions ?? []).find((item) => item.status === 'PLANNED' && item.autonomy_level === 'ASK_ONCE');
      expect(action, 'A waiting command must expose its actual planned ASK action').toBeTruthy();
      const actual = action!; const originalHash = actual.effect_hash;
      expect(actual.receipt).toBeNull(); expect(confirmed.has(actual.action_id)).toBe(false);
      const result = this.page.getByRole('region', { name: '原事件结果 FIXED_EARLY_WITHDRAWAL', exact: true });
      const card = result.locator('article.demo-action').filter({ has: this.page.getByRole('region', { name: `原动作经济后果 ${actual.action_id}`, exact: true }) });
      const button = card.getByRole('button', { name: '具体确认并执行原动作', exact: true }); await expect(button).toBeDisabled();
      if (actual.effect.action_type === 'REDEEM_ASSET') { expect(actual.effect.loss_cents).toBeGreaterThan(0); expect(actual.effect.quote_id).toBeTruthy(); }
      else expect(actual.effect.action_type).toBe('PURCHASE_ASSET');
      await this.screenshot(`fixed-ask-stage-${stage}`); const sequence = this.stateSequence;
      await card.getByRole('checkbox').check(); await button.click();
      await expect.poll(() => this.stateSequence, { timeout: 600_000 }).toBeGreaterThan(sequence); await this.settle();
      command = this.state().commands.find((item) => item.event_kind === 'FIXED_EARLY_WITHDRAWAL')!;
      const completed = (command.actions ?? []).find((item) => item.action_id === actual.action_id)!;
      expect(completed.effect_hash).toBe(originalHash); this.receipt(completed); confirmed.set(actual.action_id, originalHash);
    }
    expect(command.status).toBe('COMPLETED');
    const purchase = command.actions!.find((item) => item.effect.action_type === 'PURCHASE_ASSET')!;
    const early = command.actions!.find((item) => item.effect.action_type === 'REDEEM_ASSET')!;
    expect(purchase).toBeTruthy(); this.receipt(purchase); expect(['AUTO_EXECUTE', 'ASK_ONCE']).toContain(purchase.autonomy_level);
    if (purchase.autonomy_level === 'ASK_ONCE') expect(confirmed.get(purchase.action_id)).toBe(purchase.effect_hash);
    expect(early).toBeTruthy(); expect(early.autonomy_level).toBe('ASK_ONCE'); expect(early.effect.loss_cents).toBeGreaterThan(0);
    expect(early.effect.quote_id).toBeTruthy(); expect(early.effect.position_id).toBe(purchase.effect.position_id);
    expect(confirmed.get(early.action_id)).toBe(early.effect_hash); this.receipt(early);
    expect(early.receipt!.loss_cents).toBe(early.effect.loss_cents); expect(early.receipt!.fee_cents).toBe(early.effect.fee_cents);
    // This preset requires actual ordinary consumption after purchase; positive liquidity alone cannot authorize early loss.
    const prerequisite = command.fact;
    expect(prerequisite, 'The fixed preset must disclose its actual liquidity-consumption prerequisite').toBeTruthy();
    expect(prerequisite!.simulation).toBe(true); expect(prerequisite!.external_fact_id).toBeTruthy();
    expect(prerequisite!.bank_status).toBe('SETTLED'); expect(prerequisite!.projection_status).toBe('PROJECTED');
    expect(prerequisite!.economic_posting_ids).toHaveLength(2); expect(new Set(prerequisite!.economic_posting_ids).size).toBe(2); expect(prerequisite!.transaction_id).toBeTruthy();
    const transactions = await this.get<components['schemas']['TransactionPage']>('/transactions?category=EXTERNAL_CONSUMPTION&limit=200');
    const ordinary = transactions.items.find((item) => item.id === prerequisite!.transaction_id);
    expect(ordinary, 'The disclosed consumption must have its original projected transaction').toBeTruthy();
    expect(ordinary!.direction).toBe('DEBIT'); expect(ordinary!.category).toBe('EXTERNAL_CONSUMPTION');
    expect(ordinary!.source_ref).toBe(`external-bank-fact:${prerequisite!.external_fact_id}`); expect(ordinary!.evidence_id).toBeTruthy();
    expect(ordinary!.counterparty_ref).toBe('merchant'); expect(ordinary!.amount_cents).toBeGreaterThan(0);
    expect(ordinary!.balance_after_cents).not.toBeNull(); expect(ordinary!.balance_after_cents!).toBeGreaterThanOrEqual(0);
    await this.checkpoint('fixed-real-levels-and-loss-confirmation'); return command;
  }
  async rent(oldAction = false) {
    const template = await this.template('RENT');
    let original: Action | undefined;
    if (oldAction) {
      const prepared = await broker(this.scenario, 'prepare_rent_old_action', { expected_epoch_id: this.state().epoch_id, policy_id: template.confirmed_policy_id });
      original = await this.get<Action>(`/actions/${String(prepared.action_id)}`);
      expect(original.status).toBe('PLANNED'); expect(original.effect.policy_id).toBe(template.confirmed_policy_id);
      expect(original.effect.policy_version_id).toBe(prepared.policy_version_id); expect(original.receipt).toBeNull();
    }
    const command = await this.event('CHANGE_RENT', 'WAITING_POLICY_CHANGE');
    const review = this.page.getByRole('region', { name: '房租原修改复核', exact: true });
    await review.getByRole('button', { name: '读取房租修改边界预览', exact: true }).click(); await this.settle();
    const button = review.getByRole('button', { name: '确认房租原修改', exact: true }); await expect(button).toBeDisabled();
    const sequence = this.stateSequence; await review.getByRole('checkbox').check(); await button.click();
    await expect.poll(() => this.stateSequence, { timeout: 600_000 }).toBeGreaterThan(sequence); await this.settle();
    expect(this.state().commands.find((item) => item.command_id === command.command_id)?.status).toBe('COMPLETED');
    if (original) {
      const invalidated = await this.get<Action>(`/actions/${original.action_id}`);
      expect(invalidated.status).toBe('INVALIDATED'); expect(invalidated.effect_hash).toBe(original.effect_hash); expect(invalidated.receipt).toBeNull();
    }
    await this.checkpoint('rent-real-version-change');
  }
  async verifyRound() {
    expect(this.state().commands).toHaveLength(6);
    expect(this.state().commands.every((command) => command.status === 'COMPLETED' && command.epoch_id === this.state().epoch_id)).toBe(true);
    await broker(this.scenario, 'verify_round', { expected_epoch_id: this.state().epoch_id });
    await this.screenshot('completed-real-round');
  }
}

test.describe('W1 guarded real Edge business acceptance', () => {
  test.skip(!enabled, 'NOT_RUN: requires scripts/w1_browser_acceptance.py isolated coordinator');
  test.describe.configure({ mode: 'default' });
  let ui: RealUI;
  test.beforeEach(async ({ page }, info) => {
    const scenario = `w1-${info.testId.replace(/[^a-z0-9]/gi, '').slice(0, 64)}`;
    ui = new RealUI(page, info, scenario); await ui.start(); await ui.reset('independent-case-start');
  });
  test.afterEach(async () => { if (ui) await ui.settle(); });

  test('工资到账至真实目标分配与自动申购', async () => {
    const salary = await ui.core(); await ui.checkpoint('replay-before', 'BEGIN');
    const replay = await ui.event('SALARY_RECEIVED');
    expect((replay.actions ?? []).map((action) => action.receipt?.receipt_id)).toEqual((salary.actions ?? []).map((action) => action.receipt?.receipt_id));
    await ui.checkpoint('salary-replay-zero-write', 'READ_ONLY'); await ui.screenshot('salary-original-receipts');
  });
  test('自然语言新目标修订确认、零归属建立与后续新收入授权分配', async () => {
    await ui.checkpoint('goal-before', 'BEGIN'); await ui.page.goto('/#policies');
    const originalText = '明年十月前想攒三万买车，每个月尽量存两千，资金别锁太久。';
    await ui.page.getByLabel('策略描述', { exact: true }).fill(originalText);
    const compilationResponse = ui.page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/policies/compile', { timeout: 600_000 });
    await ui.page.getByRole('button', { name: '编译候选', exact: true }).click();
    const originalCompilationResponse = await compilationResponse; expect(originalCompilationResponse.ok()).toBe(true); await ui.settle();
    expect(originalCompilationResponse.request().postDataJSON()).toEqual({ text: originalText, engine: 'rules' });
    const compiled = await originalCompilationResponse.json() as components['schemas']['CompilationResponse'];
    expect(compiled.simulation).toBe(true); expect(compiled.compilation_id).toBeTruthy();
    expect(compiled.configuration).toBeNull(); expect(compiled.proposal_id).toBeNull();
    const referenceYear = Number(compiled.compilation.reference_date.slice(0, 4)); expect(Number.isSafeInteger(referenceYear)).toBe(true);
    const expectedDeadline = `${referenceYear + 1}-09-30`;
    expect(compiled.compilation.draft).toEqual({ type: 'goal_saving', name: '买车', target_cents: 3_000_000, deadline: expectedDeadline, monthly_contribution: { target_cents: 200000 } });
    expect(compiled.compilation.issues).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'MISSING_FIELD', field: 'monthly_contribution.min_cents' }),
      expect.objectContaining({ code: 'MISSING_FIELD', field: 'monthly_contribution.max_cents' }),
      expect.objectContaining({ code: 'UNRESOLVED_LOCK_LIMIT', field: 'max_lock_days' }),
    ]));
    const section = ui.page.getByRole('region', { name: '自然语言候选', exact: true });
    const confirm = section.getByRole('button', { name: '确认编译策略', exact: true }); await expect(confirm).toBeDisabled();
    await expect(section).toContainText('最长锁定天数');
    await expect(section.getByLabel('目标金额（元）', { exact: true })).toHaveValue('30000.00');
    await expect(section.getByLabel('目标截止日期', { exact: true })).toHaveValue(expectedDeadline);
    await expect(section.getByLabel('每月目标金额（元）', { exact: true })).toHaveValue('2000.00');
    const unconfirmedPolicies = await ui.get<components['schemas']['PolicyList']>('/policies');
    expect(unconfirmedPolicies.items.some((policy) => policy.policy_type === 'goal_saving')).toBe(false);
    await ui.screenshot('natural-goal-original-text-unresolved-limits');
    // These missing amounts are explicit user choices, not inferred permissions.
    await section.getByLabel('每月最低金额（元）', { exact: true }).fill('1800.01');
    await section.getByLabel('每月最高金额（元）', { exact: true }).fill('2500.00');
    await section.getByLabel('重要程度（0–100）', { exact: true }).fill('55');
    await expect(confirm).toBeDisabled();
    const revisionResponse = ui.page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/v1/policy-compilations/${compiled.compilation_id}/revise`, { timeout: 600_000 });
    await section.getByRole('button', { name: '保存修订候选', exact: true }).click();
    const actualRevisionResponse = await revisionResponse; expect(actualRevisionResponse.ok()).toBe(true); await ui.settle();
    const revised = await actualRevisionResponse.json() as components['schemas']['CompilationResponse'];
    expect(revised.compilation_id).toBe(compiled.compilation_id); expect(revised.compilation).toEqual(compiled.compilation);
    expect(revised.proposal_id).toBeTruthy(); expect(revised.proposal_status).toBe('PROPOSED'); expect(revised.configuration_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(revised.configuration).toMatchObject({ type: 'goal_saving', name: '买车', target_cents: 3_000_000, deadline: expectedDeadline,
      monthly_contribution: { min_cents: 180001, target_cents: 200000, max_cents: 250000 },
      priority: { importance: 55, minimum_cents: 0, reducible: false, deferrable: false },
      cross_goal_reallocation_allowed: false, asset_policy_id: null });
    expect(actualRevisionResponse.request().postDataJSON()).toEqual({ configuration: revised.configuration });
    const review = section.locator('.config-review');
    for (const label of ['目标金额', '目标截止日期', '每月最低金额', '每月目标金额', '每月最高金额', '重要程度（0–100）', '最低保护金额', '允许降低承诺', '允许延期', '关联资产策略编号']) await expect(review.getByText(label, { exact: true })).toBeVisible();
    await expect(review).toContainText('未设置'); await expect(confirm).toBeDisabled();
    const consent = section.getByRole('checkbox', { name: /我已逐项复核完整配置与原编译/ }); await expect(consent).not.toBeChecked();
    await ui.checkpoint('natural-goal-candidate-has-no-money'); await ui.screenshot('natural-goal-complete-user-revision-before-confirm');
    await consent.check();
    const firstConfirmation = ui.page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/v1/policy-proposals/${revised.proposal_id}/confirm`, { timeout: 600_000 });
    await confirm.click(); const confirmedResponse = await firstConfirmation; expect(confirmedResponse.ok()).toBe(true); await ui.settle();
    expect(confirmedResponse.request().postDataJSON()).toEqual({ accepted: true, reviewed_hash: revised.configuration_hash });
    const firstReceipt = await confirmedResponse.json() as components['schemas']['LifecycleResult'];
    expect(firstReceipt.simulation).toBe(true); expect(firstReceipt.previous_version_id).toBeNull(); expect(firstReceipt.effective_status).toBe('ACTIVE');
    const actualPolicies = await ui.get<components['schemas']['PolicyList']>('/policies');
    const actualPolicy = actualPolicies.items.find((policy) => policy.id === firstReceipt.policy_id)!;
    expect(actualPolicy, 'Original first confirmation must create the current owned policy').toBeTruthy();
    expect(actualPolicy.policy_type).toBe('goal_saving'); expect(actualPolicy.effective_status).toBe('ACTIVE'); expect(actualPolicy.version_authorized).toBe(true);
    expect(actualPolicy.current_version?.id).toBe(firstReceipt.current_version_id); expect(actualPolicy.current_version?.version_number).toBe(1);
    expect(actualPolicy.current_version?.configuration).toEqual(revised.configuration); expect(actualPolicy.current_version?.content_hash).toBe(revised.configuration_hash);
    expect(actualPolicy.current_version?.confirmation).toMatchObject({ accepted: true, reviewed_hash: revised.configuration_hash });
    expect(actualPolicy.current_version?.confirmed_at).toBeTruthy();
    await expect(ui.page.getByRole('article', { name: '策略 买车', exact: true })).toContainText('生效中');
    await ui.screenshot('natural-goal-first-confirmation-current-active');
    await ui.page.goto('/#goals'); await ui.settle();
    const card = ui.page.getByRole('article', { name: '待建立目标 买车', exact: true });
    const accounts = await ui.get<components['schemas']['AccountSummary']>('/accounts/summary');
    const account = accounts.accounts.find((item) => item.account_type === 'GOAL')!; expect(account).toBeTruthy();
    await card.getByLabel('目标归属账户', { exact: true }).selectOption(account.id);
    await expect(card.getByRole('button', { name: '建立目标归属', exact: true })).toBeDisabled();
    await card.getByRole('checkbox').check();
    const creationResponse = ui.page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/goals', { timeout: 600_000 });
    await card.getByRole('button', { name: '建立目标归属', exact: true }).click(); const createdResponse = await creationResponse; expect(createdResponse.ok()).toBe(true); await ui.settle();
    expect(createdResponse.request().postDataJSON()).toEqual({ policy_id: actualPolicy.id, expected_version_id: actualPolicy.current_version!.id, account_id: account.id });
    const goals = await ui.get<components['schemas']['GoalList']>('/goals'); expect(goals.items).toHaveLength(1);
    expect(goals.items[0]?.allocated_cents).toBe(0); expect(goals.items[0]?.monthly_min_cents).toBe(180001); expect(goals.items[0]?.account_id).toBe(account.id);
    expect(goals.items[0]?.policy_id).toBe(actualPolicy.id); expect(goals.items[0]?.policy_version_id).toBe(firstReceipt.current_version_id);
    expect(goals.items[0]?.target_cents).toBe(3_000_000); expect(goals.items[0]?.deadline).toBe(expectedDeadline);
    expect(goals.items[0]?.monthly_target_cents).toBe(200000); expect(goals.items[0]?.monthly_max_cents).toBe(250000); expect(goals.items[0]?.importance).toBe(55);
    const zeroOwned = await ui.get<components['schemas']['DashboardResponse']>('/dashboard');
    expect(zeroOwned.goal_ownership.state).toBe('PROVEN'); expect(zeroOwned.goal_ownership.items.find((item) => item.goal_id === goals.items[0]!.id)?.allocated_cents).toBe(0);
    await ui.checkpoint('goal-confirmed-zero-owned'); await ui.screenshot('natural-goal-confirmed');
    let goal = goals.items[0]!;
    // The unresolved lock preference needs separate real user authorization.
    // It is not inferred by the NL compiler and it does not allocate any money.
    await ui.page.goto('/#demo'); await ui.settle();
    const additionalAssetTemplate = await ui.template('LIQUID_ASSET');
    const templatePolicies = await ui.get<components['schemas']['PolicyList']>('/policies');
    const declaredAsset = templatePolicies.items.find((policy) => policy.id === additionalAssetTemplate.confirmed_policy_id)!;
    expect(declaredAsset).toBeTruthy(); expect(declaredAsset.policy_type).toBe('asset_authorization');
    expect(declaredAsset.current_version?.version_number).toBe(1); expect(declaredAsset.current_version?.confirmed_at).toBeTruthy();
    expect(declaredAsset.current_version?.configuration.scope).toBe('general_idle_funds');
    const explicitChanges: components['schemas']['LifecycleResult'][] = [];
    const changeThroughUI = async (policy: components['schemas']['PolicyView'], fill: (panel: ReturnType<Page['getByRole']>) => Promise<void>, reason: string) => {
      await ui.page.goto('/#policies'); await ui.settle();
      await ui.page.getByRole('article', { name: `策略 ${policy.name}`, exact: true }).getByRole('button', { name: '修改策略', exact: true }).click();
      const editor = ui.page.getByRole('region', { name: '修改策略', exact: true }); await fill(editor);
      const previewWait = ui.page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/v1/policies/${policy.id}/change-preview`, { timeout: 600_000 });
      await editor.getByRole('button', { name: '预览修改影响', exact: true }).click();
      const previewResponse = await previewWait; expect(previewResponse.ok()).toBe(true); await ui.settle();
      const preview = await previewResponse.json() as components['schemas']['PolicyChangePreviewResponse'];
      expect(preview.preview_only).toBe(true); expect(preview.policy_id).toBe(policy.id); expect(preview.expected_version_id).toBe(policy.current_version!.id);
      const submit = editor.getByRole('button', { name: '确认修改', exact: true }); await expect(submit).toBeDisabled();
      await editor.getByLabel('修改原因', { exact: true }).fill(reason);
      const acceptChange = editor.getByRole('checkbox', { name: /我已复核完整配置与本次服务端边界比较/ }); await expect(acceptChange).not.toBeChecked();
      await ui.screenshot(`natural-goal-extra-authority-review-${explicitChanges.length + 1}`);
      await acceptChange.check();
      const mutationWait = ui.page.waitForResponse((response) => response.request().method() === 'PATCH' && new URL(response.url()).pathname === `/api/v1/policies/${policy.id}`, { timeout: 600_000 });
      await submit.click(); const mutationResponse = await mutationWait; expect(mutationResponse.ok()).toBe(true); await ui.settle();
      expect(mutationResponse.request().postDataJSON()).toMatchObject({ accepted: true, expected_version_id: policy.current_version!.id, reviewed_hash: preview.configuration_hash, configuration: preview.configuration, reason });
      const receipt = await mutationResponse.json() as components['schemas']['LifecycleResult'];
      expect(receipt.policy_id).toBe(policy.id); expect(receipt.previous_version_id).toBe(policy.current_version!.id); expect(receipt.current_version_id).not.toBe(receipt.previous_version_id); expect(receipt.effective_status).toBe('ACTIVE');
      const current = (await ui.get<components['schemas']['PolicyList']>('/policies')).items.find((item) => item.id === policy.id)!;
      expect(current.version_authorized).toBe(true); expect(current.current_version?.id).toBe(receipt.current_version_id);
      expect(current.current_version?.version_number).toBe(policy.current_version!.version_number + 1);
      expect(current.current_version?.previous_hash).toBe(policy.current_version!.content_hash);
      expect(current.current_version?.configuration).toEqual(preview.configuration); expect(current.current_version?.content_hash).toBe(preview.configuration_hash);
      explicitChanges.push(receipt); return current;
    };
    const goalAsset = await changeThroughUI(declaredAsset, async (editor) => {
      await editor.getByLabel('资产授权范围', { exact: true }).selectOption('goal');
      await editor.getByLabel('目标编号', { exact: true }).fill(goal.id);
      await editor.getByLabel('最长锁定天数', { exact: true }).fill('0');
      await editor.getByLabel('最长赎回到账天数', { exact: true }).fill('0');
    }, '用户另外明确：只管理本买车目标，允许当日到账现金管理，最长锁定0天、到账0天；不从自然语言推断权限');
    expect(goalAsset.current_version?.configuration).toMatchObject({ scope: 'goal', goal_id: goal.id, allowed_asset_classes: ['CASH_MGMT_T0'], max_lock_days: 0, max_redemption_delay_days: 0 });
    const allocationPolicy = await changeThroughUI(actualPolicy, async (editor) => {
      await editor.getByLabel('关联资产策略编号（可选）', { exact: true }).fill(goalAsset.id);
    }, '用户另外明确关联本目标的已确认资产授权；原NL首次确认版本保留，不把关联步骤称自动生成');
    expect(allocationPolicy.current_version?.configuration).toEqual({ ...revised.configuration, asset_policy_id: goalAsset.id });
    await ui.page.goto('/#goals'); await ui.settle();
    goal = (await ui.get<components['schemas']['GoalList']>('/goals')).items.find((item) => item.id === goal.id)!;
    expect(goal.policy_version_id).toBe(allocationPolicy.current_version!.id); expect(goal.asset_policy_id).toBe(goalAsset.id); expect(goal.allocated_cents).toBe(0);
    const configuredGoal = ui.page.getByRole('article', { name: '目标 买车', exact: true });
    await configuredGoal.getByText('查看关联资产策略', { exact: true }).click();
    const assetDetails = configuredGoal.locator('details').filter({ has: ui.page.getByText('查看关联资产策略', { exact: true }) });
    for (const label of ['允许资产类别', '最长锁定天数', '最长赎回到账天数', '本金风险等级上限（0–5）', '允许无损自动恢复', '允许有损提前退出']) await expect(assetDetails.getByText(label, { exact: true })).toBeVisible();
    await expect(assetDetails).toContainText('当日到账现金管理'); await expect(assetDetails).toContainText(goalAsset.id);
    expect(explicitChanges).toHaveLength(2); await ui.checkpoint('natural-goal-extra-confirmations-zero-owned');
    await ui.screenshot('natural-goal-extra-confirmed-asset-tolerance');
    const income = await broker(ui.scenario, 'ingest_goal_income', { expected_epoch_id: ui.state().epoch_id, goal_id: goal.id }) as unknown as GoalIncomeRead;
    expect(income.goal_id).toBe(goal.id); expect(income.scope).toBeTruthy(); expect(income.request.kind).toBe('INCOME');
    expect(income.request.amount_cents).toBe(200000); expect(income.request.counterparty_ref).toBe('payroll');
    expect(income.request.occurred_at).toBeTruthy(); expect(income.request.external_ref).toBeTruthy();
    expect(Date.parse(income.request.occurred_at)).toBeGreaterThan(Date.parse(allocationPolicy.current_version!.confirmed_at!));
    expect(income.result.simulation).toBe(true); expect(income.result.bank_status).toBe('SETTLED'); expect(income.result.projection_status).toBe('PROJECTED');
    expect(income.result.economic_posting_ids).toHaveLength(2); expect(income.result.transaction_id).toBeTruthy();
    expect(new Set(income.result.economic_posting_ids).size).toBe(2);
    const salaries = await ui.get<components['schemas']['TransactionPage']>('/transactions?category=SALARY&limit=200');
    const salaryFact = salaries.items.find((item) => item.id === income.result.transaction_id)!;
    expect(salaryFact, 'Later fixed payroll must have its actual independent bank transaction').toBeTruthy();
    expect(salaryFact.direction).toBe('CREDIT'); expect(salaryFact.category).toBe('SALARY'); expect(salaryFact.amount_cents).toBe(200000);
    expect(salaryFact.account_id).toBe(income.request.account_id); expect(salaryFact.counterparty_ref).toBe('payroll');
    expect(salaryFact.source_ref).toBe(`external-bank-fact:${income.result.external_fact_id}`); expect(salaryFact.evidence_id).toBeTruthy();
    expect(Date.parse(salaryFact.occurred_at)).toBe(Date.parse(income.request.occurred_at));
    const unallocatedIncome = await ui.get<components['schemas']['GoalList']>('/goals'); expect(unallocatedIncome.items.find((item) => item.id === goal.id)!.allocated_cents).toBe(0);
    await ui.checkpoint('new-goal-later-fixed-income');
    await ui.page.getByRole('button', { name: '刷新目标', exact: true }).click(); await ui.settle();
    const owned = ui.page.getByRole('article', { name: '目标 买车', exact: true });
    await owned.getByRole('button', { name: '查看当前收入分配预览', exact: true }).click(); await ui.settle();
    const prepare = owned.getByRole('button', { name: '准备当前收入分配', exact: true }); await expect(prepare).toBeEnabled();
    const preparation = ui.page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/actions/prepare', { timeout: 600_000 });
    await prepare.click(); const preparedResponse = await preparation; expect(preparedResponse.ok()).toBe(true); await ui.settle();
    const prepared = await preparedResponse.json() as Action;
    expect(prepared.effect.action_type).toBe('ALLOCATE_GOAL'); expect(prepared.effect.goal_id).toBe(goal.id);
    expect(prepared.effect.policy_id).toBe(goal.policy_id); expect(prepared.effect.policy_version_id).toBe(goal.policy_version_id); expect(prepared.effect.destination_account_id).toBe(goal.account_id);
    expect(prepared.effect.amount_cents).toBeGreaterThan(0); expect(prepared.effect.amount_cents).toBeLessThanOrEqual(200000); expect(prepared.receipt).toBeNull();
    expect(prepared.effect.income_uses?.length).toBeGreaterThan(0);
    expect(prepared.effect.income_uses?.every((use) => use.origin_transaction_id === income.result.transaction_id && use.account_id === income.request.account_id)).toBe(true);
    expect(prepared.effect.income_uses?.reduce((sum, use) => sum + use.amount_cents, 0)).toBe(prepared.effect.amount_cents);
    await expect(owned.getByRole('region', { name: `原动作经济后果 ${prepared.action_id}`, exact: true })).toContainText(prepared.effect_hash);
    const onlyPrepared = await ui.get<components['schemas']['GoalList']>('/goals'); expect(onlyPrepared.items.find((item) => item.id === goal.id)!.allocated_cents).toBe(0);
    await ui.screenshot('natural-goal-original-income-action-review');
    if (prepared.autonomy_level === 'ASK_ONCE') {
      const execute = owned.getByRole('button', { name: '具体确认并执行原分配', exact: true }); await expect(execute).toBeDisabled();
      const consent = owned.getByRole('checkbox', { name: /我已复核此原分配/ }); await expect(consent).not.toBeChecked(); await consent.check(); await execute.click();
    } else { expect(prepared.autonomy_level).toBe('AUTO_EXECUTE'); await owned.getByRole('button', { name: '执行已复核的原分配', exact: true }).click(); }
    await ui.settle(); await expect(owned).toContainText('实际原回执');
    const completed = await ui.get<Action>(`/actions/${prepared.action_id}`); expect(completed.effect_hash).toBe(prepared.effect_hash); ui.receipt(completed);
    expect(completed.action_id).toBe(prepared.action_id); expect(completed.effect).toEqual(prepared.effect); expect(completed.autonomy_level).toBe(prepared.autonomy_level);
    expect(completed.receipt!.executed_cents).toBe(prepared.effect.amount_cents); expect(completed.receipt!.fee_cents).toBe(0); expect(completed.receipt!.loss_cents).toBe(0);
    const allocated = await ui.get<components['schemas']['GoalList']>('/goals'); expect(allocated.items.find((item) => item.id === goal.id)!.allocated_cents).toBe(completed.receipt!.executed_cents);
    const dashboard = await ui.get<components['schemas']['DashboardResponse']>('/dashboard');
    expect(dashboard.goal_ownership.state).toBe('PROVEN'); expect(dashboard.goal_ownership.items.find((item) => item.goal_id === goal.id)!.cash_owned_cents).toBe(completed.receipt!.executed_cents);
    const trace = await ui.get<components['schemas']['DecisionTraceResponse']>(`/decisions/${completed.decision_run_id}`); expect(trace.audit_chain_status).toBe('VALID');
    expect(trace.completeness).toBe('COMPLETE'); expect(trace.trace?.action_id).toBe(completed.action_id);
    const originalTrace = trace.trace;
    if (!originalTrace?.sources || !originalTrace.policies) throw new Error('The actual complete trace must contain original sources and policy versions');
    const originalSalaryEvidence = originalTrace.sources.find((source) => source.id === salaryFact.evidence_id)!;
    expect(originalSalaryEvidence, 'The captured decision must include this original payroll bank proof').toBeTruthy();
    expect(originalSalaryEvidence.evidence_level).toBe('BANK_CONFIRMED'); expect(originalSalaryEvidence.source_type).toBe('SIMULATED_BANK_TRANSACTION');
    expect(originalSalaryEvidence.source_ref).toBe(salaryFact.source_ref); expect(originalSalaryEvidence.content_integrity).toBe('VERIFIED');
    expect(originalSalaryEvidence.content).toMatchObject({ transaction_id: salaryFact.id, account_id: salaryFact.account_id, direction: 'CREDIT',
      amount_cents: 200000, counterparty_ref: 'payroll', economic_role: 'INCOME', external_fact_id: income.result.external_fact_id });
    const capturedPolicy = originalTrace.policies.find((policy) => policy.id === allocationPolicy.current_version!.id)!;
    expect(capturedPolicy).toBeTruthy(); expect(capturedPolicy.policy_id).toBe(allocationPolicy.id); expect(capturedPolicy.configuration_hash).toBe(allocationPolicy.current_version!.content_hash);
    expect(capturedPolicy.configuration).toEqual(allocationPolicy.current_version!.configuration); expect(capturedPolicy.configuration_integrity).toBe('VERIFIED');
    await ui.checkpoint('natural-goal-later-income-real-allocation'); await ui.screenshot('natural-goal-later-income-original-receipt');
    // This is an observed scoped coverage record, never an MVP completion flag.
    await writeFile(ui.info.outputPath('golden-b-observed-scope.json'), JSON.stringify({
      classification: 'ACTUAL_BROWSER_SCOPED_COVERAGE', original_input: originalText, scenario_id: ui.scenario,
      run_id: runDirectory?.split(/[\\/]/).at(-1), epoch_id: ui.state().epoch_id,
      compilation_id: compiled.compilation_id, proposal_id: revised.proposal_id, reviewed_hash: revised.configuration_hash,
      policy_id: actualPolicy.id, first_confirmed_version_id: actualPolicy.current_version!.id,
      allocation_policy_version_id: allocationPolicy.current_version!.id, goal_id: goal.id,
      additional_asset_template_first_confirmed_policy_id: declaredAsset.id,
      additional_asset_template_first_confirmed_version_id: declaredAsset.current_version!.id,
      additional_asset_scoped_version_id: goalAsset.current_version!.id,
      actual_additional_user_policy_confirmations: explicitChanges.length + 1, explicit_additional_version_receipts: explicitChanges,
      original_compilation_issues: compiled.compilation.issues, original_compilation_assumptions: compiled.compilation.assumptions,
      later_income_external_fact_id: income.result.external_fact_id, later_income_transaction_id: salaryFact.id,
      later_income_bank_evidence_id: salaryFact.evidence_id, later_income_bank_posting_ids: income.result.economic_posting_ids,
      action_id: completed.action_id, effect_hash: completed.effect_hash, original_receipt_id: completed.receipt!.receipt_id,
      actual_autonomy_level: completed.autonomy_level,
      automatic_allocation_scope: completed.autonomy_level === 'AUTO_EXECUTE' ? 'AUTO_EXECUTE_OBSERVED' : 'ASK_ONCE_ACTUAL_NOT_AUTOMATIC',
      asset_tolerance_and_class_review: 'SEPARATE_EXPLICIT_USER_AUTHORITY_AND_GOAL_VERSION_LINK_OBSERVED',
      original_lock_limit_issue_retained: 'UNRESOLVED_LOCK_LIMIT',
      t1_demo_content: 'NOT_COVERED', experiment_comparison: 'NOT_COVERED',
      full_mvp_acceptance: 'UNVERIFIED', task_closed: false,
    }, null, 2), { flag: 'wx' });
  });
  test('大额消费触发合法自动无损赎回', async () => { await ui.core(); await ui.recovery(); await ui.screenshot('consumption-actual-recovery'); });
  test('定存损失单独询问并绑定原报价回执', async () => { await ui.core(); await ui.recovery(); await ui.fixed(); await ui.screenshot('fixed-loss-original-receipt'); });
  test('修改房租后绑定旧版本的原动作失效', async () => { await ui.rent(true); await ui.screenshot('rent-old-action-invalidated'); });
  test('所选真实原动作审计链VALID且浏览零写', async () => {
    const salary = await ui.core(); const action = (salary.actions ?? [])[0]!; await ui.checkpoint('audit-before', 'BEGIN');
    await ui.page.goto(`/#decisions/${action.decision_run_id}`); await ui.settle();
    await expect(ui.page.getByRole('region', { name: '8 审计哈希状态', exact: true })).toContainText('VALID');
    const trace = await ui.get<components['schemas']['DecisionTraceResponse']>(`/decisions/${action.decision_run_id}`);
    expect(trace.audit_chain_status).toBe('VALID'); expect(trace.run_id).toBe(action.decision_run_id);
    await ui.checkpoint('audit-get-zero-write', 'READ_ONLY'); await ui.screenshot('original-audit-valid');
  });
  test('同一隔离库七按钮连续三轮及三次真实UI重置', async () => {
    test.setTimeout(10_800_000); const epochs = new Set<string>();
    for (let round = 1; round <= 3; round++) {
      const epoch = ui.state().epoch_id!; expect(epochs.has(epoch)).toBe(false); epochs.add(epoch);
      await test.step(`actual round ${round}`, async () => { await ui.core(); await ui.recovery(); await ui.fixed(); await ui.rent(); await ui.verifyRound(); });
      await ui.reset(`round-${round}-reset`);
    }
    expect(epochs.size).toBe(3); await ui.screenshot('after-third-real-reset');
  });
});
