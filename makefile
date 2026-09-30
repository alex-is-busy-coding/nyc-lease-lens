ifneq (,$(wildcard .env))
include .env
export
endif

UV_RUN := uv run

.DEFAULT_GOAL := help
.PHONY: help setup env install login gcp-setup auth-check run dev clean require-gcloud

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: env install login gcp-setup ## First-time setup: .env, deps, gcloud login, enable Vertex AI

env: ## Create .env from .env.example
	@if [ -f .env ]; then echo ".env already exists; left unchanged"; else $(MAKE) --no-print-directory .env; fi

.env:
	@cp .env.example .env
	@echo "Created .env from .env.example"

install: .env ## Install dependencies from uv.lock
	uv sync

login: require-gcloud ## Log in to Google Cloud and point ADC at the project
	gcloud auth application-default login
	gcloud auth application-default set-quota-project $(VERTEXAI_PROJECT)
	gcloud config set project $(VERTEXAI_PROJECT)

gcp-setup: require-gcloud ## Enable the Vertex AI API on the project
	gcloud services enable aiplatform.googleapis.com --project $(VERTEXAI_PROJECT)

auth-check: require-gcloud ## Verify credentials work before starting the app
	@gcloud auth application-default print-access-token >/dev/null \
		&& echo "ADC OK (project: $(VERTEXAI_PROJECT))" \
		|| { echo "No valid credentials. Run: make login"; exit 1; }

run: .env auth-check ## Start the app
	$(UV_RUN) python -m nyc_lease_lens

dev: .env auth-check ## Start the app with auto-reload
	RELOAD=true $(UV_RUN) python -m nyc_lease_lens

clean: ## Remove the virtualenv and caches
	rm -rf .venv
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

require-gcloud:
	@command -v gcloud >/dev/null || { echo "gcloud not found. Install: brew install --cask google-cloud-sdk"; exit 1; }
