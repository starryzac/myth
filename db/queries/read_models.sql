-- Generated read adapters deliberately retain environment/user predicates.
-- Production T1/T2/T3 remain in the application; generation grants no authority.

-- name: GetEnvironmentByNamespace :one
SELECT id, namespace, writer_generation, control_version
FROM environments WHERE namespace = sqlc.arg(namespace);

-- name: LockOwnedUserFunds :one
SELECT funds_version, source_version FROM users
WHERE environment_id = sqlc.arg(environment_id) AND id = sqlc.arg(user_id)
FOR UPDATE;

-- name: ListOwnedAccounts :many
SELECT id, name, balance_cents, goal_owned_cents FROM accounts
WHERE environment_id = sqlc.arg(environment_id) AND user_id = sqlc.arg(user_id)
ORDER BY id LIMIT 100;

-- name: ListOwnedGoals :many
SELECT id, name, account_id, target_cents, assigned_cents FROM goals
WHERE environment_id = sqlc.arg(environment_id) AND user_id = sqlc.arg(user_id)
ORDER BY id LIMIT 100;

-- name: GetOwnedCurrentPolicyOriginal :one
SELECT version, status, original, confirmation_id FROM policies
WHERE environment_id = sqlc.arg(environment_id) AND user_id = sqlc.arg(user_id)
  AND id = sqlc.arg(policy_id) AND is_current;

-- name: GetOwnedCurrentGrantOriginal :one
SELECT version, policy_id, policy_version, status, original, confirmation_id FROM grants
WHERE environment_id = sqlc.arg(environment_id) AND user_id = sqlc.arg(user_id)
  AND id = sqlc.arg(grant_id) AND is_current;

-- name: GetOwnedOperation :one
SELECT id, plan_id, status, bank_key, bank_observed_status, processing_health,
  receipt_id, created_at, updated_at, version, bank_request
FROM operations
WHERE environment_id = sqlc.arg(environment_id) AND user_id = sqlc.arg(user_id)
  AND id = sqlc.arg(operation_id);

-- name: GetOwnedOperationByCommandKey :one
SELECT id, plan_id, status, bank_key, bank_observed_status, processing_health,
  receipt_id, created_at, updated_at, version, bank_request
FROM operations
WHERE environment_id = sqlc.arg(environment_id) AND user_id = sqlc.arg(user_id)
  AND command_kind = 'ALLOCATE_GOAL' AND idempotency_key = sqlc.arg(command_key);

-- name: ListOwnedRecentOperations :many
SELECT id, plan_id, status, bank_key, bank_observed_status, processing_health,
  receipt_id, created_at, updated_at, version, bank_request
FROM operations
WHERE environment_id = sqlc.arg(environment_id) AND user_id = sqlc.arg(user_id)
ORDER BY created_at DESC, id DESC LIMIT 20;

-- name: GetOwnedReceiptOriginal :one
SELECT id, operation_id, bank_operation_id, original, created_at
FROM receipts
WHERE environment_id = sqlc.arg(environment_id) AND user_id = sqlc.arg(user_id)
  AND operation_id = sqlc.arg(operation_id);
