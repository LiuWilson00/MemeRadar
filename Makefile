# 本機無 make 時（Windows），直接執行各目標對應的指令即可，見 README。
.PHONY: install lint test check

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check .

test:
	pytest

check: lint test
