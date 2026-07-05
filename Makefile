# FunTrade — European UCITS ETF perturbation trader (R&D)
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHON_DIR := $(ROOT)/python
SYMBOL ?= VWCE.DE
DAYS ?= 730
REFRESH_DAYS ?= 14

UV ?= uv
COMPOSE ?= docker compose

ifneq (,$(wildcard $(ROOT)/.env))
  include $(ROOT)/.env
  export
endif

.DEFAULT_GOAL := help

.PHONY: help setup build test run run-down migrate logs \
        seed ingest ingest-factors calibrate calibrate-all detect backtest paper reconcile \
        refresh grafana-reload ui components jacobian sweep compare clean

help: ## Show this help
	@echo "FunTrade — useful commands"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(ROOT)/Makefile | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make setup && make run && make seed"
	@echo "  make calibrate SYMBOL=VWCE.DE && make detect && make backtest SYMBOL=VWCE.DE"
	@echo "  make ui"

setup: ## First-time setup: copy .env + config.json, install Python deps
	@test -f $(ROOT)/.env || cp $(ROOT)/.env.example $(ROOT)/.env
	@test -f $(ROOT)/config.json || cp $(ROOT)/config.json.example $(ROOT)/config.json
	@echo "Created .env and config.json (edit watchlists / DATABASE_URL if needed)"
	$(MAKE) build

build: ## Install Python package (uv sync)
	cd $(PYTHON_DIR) && $(UV) sync --extra dev --extra ui

test: ## Run pytest (no network)
	cd $(PYTHON_DIR) && $(UV) run pytest -q

run: ## Start TimescaleDB (5433) + Grafana (3001)
	$(COMPOSE) -f $(ROOT)/docker-compose.yml up -d
	@echo "TimescaleDB: localhost:5433  Grafana: http://localhost:3001 (admin/admin)"

run-down: ## Stop Docker services
	$(COMPOSE) -f $(ROOT)/docker-compose.yml down

logs: ## Tail Docker logs
	$(COMPOSE) -f $(ROOT)/docker-compose.yml logs -f --tail=100

grafana-reload: ## Restart Grafana to load provisioned dashboards
	$(COMPOSE) -f $(ROOT)/docker-compose.yml restart grafana
	@echo "Grafana: http://localhost:3001 → Dashboards → FunTrade folder"

migrate: ## Apply SQL migrations to running DB
	@for f in $(ROOT)/sql/002_paper_trading.sql $(ROOT)/sql/003_factor_signals.sql $(ROOT)/sql/004_perturbation_daily.sql $(ROOT)/sql/005_perturbation_daily_asset_class.sql; do \
		echo "Applying $$f..."; \
		docker exec -i funtrade-timescaledb psql -U $${POSTGRES_USER:-funtrade} -d $${POSTGRES_DB:-funtrade} < "$$f"; \
	done

seed: build ## Load synthetic daily bars (offline, no API)
	cd $(PYTHON_DIR) && $(UV) run funtrade-seed --days $(DAYS)

ingest: build ## Ingest watchlist from Stooq/yfinance (needs network)
	cd $(PYTHON_DIR) && $(UV) run funtrade-ingest --days $(DAYS)

ingest-factors: build ## Ingest H0 macro factors (core + optional oil/climate from .env)
	cd $(PYTHON_DIR) && $(UV) run funtrade-ingest-factors --days $(DAYS)

calibrate: build ## Calibrate H0 OU equilibrium (SYMBOL=...)
	cd $(PYTHON_DIR) && $(UV) run funtrade-calibrate --symbol $(SYMBOL)

calibrate-all: build ## Calibrate H0 for entire WATCHLIST
	cd $(PYTHON_DIR) && $(UV) run funtrade-calibrate --all

detect: build ## Detect latest ε perturbations for watchlist
	cd $(PYTHON_DIR) && $(UV) run funtrade-detect

backtest: build ## Run walk-forward backtest (SYMBOL=...)
	cd $(PYTHON_DIR) && $(UV) run funtrade-backtest --symbol $(SYMBOL)

sweep: build ## Threshold sweep for SYMBOL
	cd $(PYTHON_DIR) && $(UV) run funtrade-backtest --symbol $(SYMBOL) --sweep

compare: build ## Strategy vs EXSA.DE buy-and-hold
	cd $(PYTHON_DIR) && $(UV) run funtrade-backtest --symbol $(SYMBOL) --compare

paper: build ## Forward paper trade (all WATCHLIST; SYMBOL=... on CLI for one)
ifeq ($(origin SYMBOL),command line)
	cd $(PYTHON_DIR) && $(UV) run funtrade-paper --symbol $(SYMBOL)
else
	cd $(PYTHON_DIR) && $(UV) run funtrade-paper
endif

reconcile: build ## Cross-check Stooq vs EOD (needs EOD_API_TOKEN)
	cd $(PYTHON_DIR) && $(UV) run funtrade-reconcile --symbol $(SYMBOL)

refresh: build ## Recent ingest + detect + paper (REFRESH_DAYS=14, needs network)
	$(MAKE) ingest DAYS=$(REFRESH_DAYS)
	$(MAKE) ingest-factors DAYS=$(REFRESH_DAYS)
	$(MAKE) detect
	$(MAKE) paper

ui: build ## Streamlit console → http://localhost:8501
	cd $(PYTHON_DIR) && $(UV) run funtrade-ui

components: build ## List H0/H1 component definitions
	cd $(PYTHON_DIR) && $(UV) run funtrade-components

jacobian: build ## Perturbation driver sensitivity (SYMBOL=...)
	cd $(PYTHON_DIR) && $(UV) run funtrade-jacobian --symbol $(SYMBOL)

clean: ## Remove Python cache and paper CSV
	find $(PYTHON_DIR) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(PYTHON_DIR)/.pytest_cache
	rm -f $(ROOT)/data/paper_trades.csv

# Convenience: full offline demo pipeline
demo: run seed calibrate detect backtest ## Offline demo (seed → calibrate → detect → backtest)
	@echo "Demo complete for $(SYMBOL). Try: make ui"

# Convenience: live data pipeline
live: run ingest ingest-factors calibrate-all detect ## Live data pipeline (needs network)
	@echo "Live ingest complete. Try: make backtest SYMBOL=$(SYMBOL)"
