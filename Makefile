.PHONY: bootstrap build prepare dev status types
bootstrap build prepare dev status types:
	python scripts/tasks.py $@
