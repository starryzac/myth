.PHONY: bootstrap dev migrate seed lint types typecheck unit property integration test e2e check demo-reset export-evidence security-check audit-verify evidence-check build-proposal policy-refresh
bootstrap dev migrate seed lint types typecheck unit property integration test e2e check demo-reset export-evidence security-check audit-verify evidence-check build-proposal policy-refresh:
	python scripts/tasks.py $@
