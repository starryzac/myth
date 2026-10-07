.PHONY: bootstrap build build-all prepare dev status types
bootstrap build build-all prepare dev status types:
	python scripts/tasks.py $@
