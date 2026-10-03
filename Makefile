.PHONY: bootstrap dev migrate seed lint types typecheck unit property integration test e2e check demo-reset export-evidence security-check audit-verify evidence-check build-proposal
bootstrap dev migrate seed lint types typecheck unit property integration test e2e check demo-reset export-evidence security-check audit-verify evidence-check build-proposal:
	python scripts/tasks.py $@
