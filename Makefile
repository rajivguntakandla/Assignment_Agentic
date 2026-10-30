.PHONY: setup test test-java test-orchestrator test-integration demo service

VENV := orchestrator/.venv
PY := $(VENV)/bin

setup:            ## create the Python venv and install the orchestrator
	python3 -m venv $(VENV)
	$(PY)/pip install -q -e "./orchestrator[dev]"

test: test-java test-orchestrator

test-java:        ## URL shortener unit + integration tests
	cd url-shortener && mvn -B -q verify

test-orchestrator: ## orchestrator lint + unit + orchestration tests (fast, no Maven)
	cd orchestrator && .venv/bin/ruff check . && .venv/bin/pytest -q

test-integration: ## every scenario end-to-end against the real Maven build
	cd orchestrator && .venv/bin/pytest -m integration -q

demo:             ## all scenarios + resilience drill; writes docs/evidence
	cd orchestrator && .venv/bin/agentic demo --llm offline

service:          ## run the URL shortener on :8080
	cd url-shortener && mvn -q spring-boot:run
