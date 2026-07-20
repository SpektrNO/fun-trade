# FunTrade — European UCITS ETF perturbation trader (R&D)
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHON_DIR := $(ROOT)/python
SYMBOL ?= VWCE.DE
DAYS ?= 730
REFRESH_DAYS ?= 14

UV ?= uv
COMPOSE ?= docker compose

# --- ngrok (local Streamlit UI, port 8501) ---
NGROK              ?= $(shell command -v ngrok 2>/dev/null || echo $(HOME)/.local/bin/ngrok)
NGROK_API          ?= http://127.0.0.1:4040
NGROK_CONFIG       ?= ngrok.yml
NGROK_LOCAL_CONFIG ?= ngrok.local.yml
NGROK_GLOBAL_CONFIG ?= $(HOME)/.config/ngrok/ngrok.yml
NGROK_TUNNEL       ?= funtrade
NGROK_DOMAIN := $(shell grep -E '^\s+domain:' $(NGROK_CONFIG) 2>/dev/null | head -1 | sed 's/.*domain:[[:space:]]*//')
NGROK_PORT   := $(or $(shell grep -E '^\s+addr:' $(NGROK_CONFIG) 2>/dev/null | head -1 | sed 's/.*addr:[[:space:]]*//'),8501)
NGROK_URL        ?= https://$(NGROK_DOMAIN)
HONEY_NGROK_LOCAL ?= $(abspath $(ROOT)/../norwegian-honey/ngrok.local.yml)

ifneq (,$(wildcard $(ROOT)/.env))
  include $(ROOT)/.env
  export
endif

.DEFAULT_GOAL := help

.PHONY: help setup build test run run-down migrate logs \
        seed ingest ingest-factors calibrate calibrate-all detect backtest paper reconcile \
        refresh grafana-reload ui fetch-profiles components jacobian sweep compare clean \
        help-ngrok ngrok-install ngrok-setup ngrok-check ngrok-tunnel ngrok-tunnel-ephemeral ngrok-url

help: ## Show this help
	@echo "FunTrade — useful commands"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(ROOT)/Makefile | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make setup && make run && make seed"
	@echo "  make calibrate SYMBOL=VWCE.DE && make detect && make backtest SYMBOL=VWCE.DE"
	@echo "  make ui"

help-ngrok: ## ngrok tunnel targets (public URL to local Streamlit)
	@echo "ngrok  →  $(NGROK_URL)  (local Streamlit: http://localhost:$(NGROK_PORT))"
	@echo ""
	@grep -hE '^ngrok-[a-zA-Z0-9_-]+:.*##' $(ROOT)/Makefile | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

setup: ## First-time setup: copy .env + config.json + portfolio.json, install Python deps
	@test -f $(ROOT)/.env || cp $(ROOT)/.env.example $(ROOT)/.env
	@test -f $(ROOT)/config.json || cp $(ROOT)/config.json.example $(ROOT)/config.json
	@test -f $(ROOT)/universe.json || cp $(ROOT)/universe.json.example $(ROOT)/universe.json
	@test -f $(ROOT)/portfolio.json || cp $(ROOT)/portfolio.json.example $(ROOT)/portfolio.json
	@echo "Created .env, config.json, universe.json, and portfolio.json (edit locally; gitignored except *.example)"
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
	@for f in $(ROOT)/sql/002_paper_trading.sql $(ROOT)/sql/003_factor_signals.sql $(ROOT)/sql/004_perturbation_daily.sql $(ROOT)/sql/005_perturbation_daily_asset_class.sql $(ROOT)/sql/006_perturbation_daily_z_trend.sql $(ROOT)/sql/007_perturbation_daily_market_regime.sql $(ROOT)/sql/008_perturbation_daily_fair_band.sql $(ROOT)/sql/009_perturbation_daily_h0_compare.sql $(ROOT)/sql/010_perturbation_daily_season_alone.sql; do \
		echo "Applying $$f..."; \
		docker exec -i funtrade-timescaledb psql -U $${POSTGRES_USER:-funtrade} -d $${POSTGRES_DB:-funtrade} < "$$f"; \
	done

seed: build ## Load synthetic daily bars (offline, no API)
	cd $(PYTHON_DIR) && $(UV) run funtrade-seed --days $(DAYS)

ingest: build ## Ingest prices (watchlist, SYMBOL=, SYMBOLS=, or CLASS='etf share')
ifeq ($(origin SYMBOLS),command line)
	cd $(PYTHON_DIR) && $(UV) run funtrade-ingest --days $(DAYS) --symbols $(SYMBOLS)
else ifeq ($(origin SYMBOL),command line)
	cd $(PYTHON_DIR) && $(UV) run funtrade-ingest --days $(DAYS) --symbol $(SYMBOL)
else ifneq ($(strip $(CLASS)),)
	cd $(PYTHON_DIR) && $(UV) run funtrade-ingest --days $(DAYS) --class $(CLASS)
else
	cd $(PYTHON_DIR) && $(UV) run funtrade-ingest --days $(DAYS)
endif

ingest-factors: build ## Ingest H0 macro factors (core + optional oil/climate from .env)
	cd $(PYTHON_DIR) && $(UV) run funtrade-ingest-factors --days $(DAYS)

calibrate: build ## Calibrate H0 OU equilibrium (SYMBOL=...)
	cd $(PYTHON_DIR) && $(UV) run funtrade-calibrate --symbol $(SYMBOL)

calibrate-all: build ## Calibrate H0 for entire WATCHLIST
	cd $(PYTHON_DIR) && $(UV) run funtrade-calibrate --all

detect: build ## Detect latest ε perturbations (CLASS='etf share' to filter)
ifneq ($(strip $(CLASS)),)
	cd $(PYTHON_DIR) && $(UV) run funtrade-detect --class $(CLASS)
else
	cd $(PYTHON_DIR) && $(UV) run funtrade-detect
endif

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

refresh: build ## Recent ingest + detect (REFRESH_DAYS=14; CLASS='etf share')
	$(MAKE) ingest DAYS=$(REFRESH_DAYS) $(if $(CLASS),CLASS="$(CLASS)")
	$(MAKE) ingest-factors DAYS=$(REFRESH_DAYS)
	$(MAKE) detect $(if $(CLASS),CLASS="$(CLASS)")

fetch-profiles: build ## Fetch fund_profiles (Nordnet slugs; EOD fallback for ETFs without slug)
ifeq ($(origin SYMBOL),command line)
	cd $(PYTHON_DIR) && $(UV) run funtrade-fetch-profiles --symbol $(SYMBOL) $(if $(NORDNET_URL),--nordnet-url "$(NORDNET_URL)")
else ifneq ($(strip $(CLASS)),)
	cd $(PYTHON_DIR) && $(UV) run funtrade-fetch-profiles --class $(CLASS)
else
	cd $(PYTHON_DIR) && $(UV) run funtrade-fetch-profiles --class etf
endif

ui: build ## Streamlit console → http://localhost:8501
	cd $(PYTHON_DIR) && $(UV) run funtrade-ui

# --- ngrok (optional — expose UI on other networks) ---

ngrok-install: ## Install ngrok to ~/.local/bin
	@mkdir -p $(HOME)/.local/bin
	curl -sSL "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz" -o /tmp/ngrok.tgz
	tar -xzf /tmp/ngrok.tgz -C $(HOME)/.local/bin ngrok
	@echo "Installed: $$($(NGROK) version)"
	@$(MAKE) ngrok-setup

ngrok-setup: ## Create ngrok.local.yml + ngrok.yml from examples
	@test -f $(NGROK_CONFIG) || cp ngrok.yml.example $(NGROK_CONFIG)
	@if [ -f $(NGROK_LOCAL_CONFIG) ]; then \
		echo "$(NGROK_LOCAL_CONFIG) exists"; \
	elif [ -f $(HONEY_NGROK_LOCAL) ]; then \
		cp "$(HONEY_NGROK_LOCAL)" $(NGROK_LOCAL_CONFIG); \
		echo "Copied authtoken from norwegian-honey → $(NGROK_LOCAL_CONFIG)"; \
	else \
		cp ngrok.local.yml.example $(NGROK_LOCAL_CONFIG); \
		echo "Edit $(NGROK_LOCAL_CONFIG) with your ngrok authtoken"; \
	fi
	@echo "Edit $(NGROK_CONFIG): set domain (or use make ngrok-tunnel-ephemeral)"
	@echo "Then: make ui  +  make ngrok-tunnel"

ngrok-check: ## Validate ngrok config
	@test -x "$(NGROK)" || (echo "run: make ngrok-install"; exit 1)
	@test -f "$(NGROK_CONFIG)" || (echo "run: make ngrok-setup"; exit 1)
	@if [ -f "$(NGROK_LOCAL_CONFIG)" ]; then \
		$(NGROK) config check --config "$(NGROK_LOCAL_CONFIG)" --config "$(NGROK_CONFIG)"; \
	elif [ -f "$(NGROK_GLOBAL_CONFIG)" ]; then \
		$(NGROK) config check --config "$(NGROK_GLOBAL_CONFIG)" --config "$(NGROK_CONFIG)"; \
	else echo "run: make ngrok-setup"; exit 1; fi

ngrok-tunnel: ## Start ngrok tunnel (run make ui in another terminal)
	@test -x "$(NGROK)" || (echo "run: make ngrok-install"; exit 1)
	@test -f "$(NGROK_CONFIG)" || (echo "run: make ngrok-setup"; exit 1)
	@echo "$(NGROK_URL) → 127.0.0.1:$(NGROK_PORT)"
	@echo "Mobile: open the root URL exactly (no /images/... path). Tap Visit Site if shown."
	@if [ -f "$(NGROK_LOCAL_CONFIG)" ]; then \
		$(NGROK) start --config "$(NGROK_LOCAL_CONFIG)" --config "$(NGROK_CONFIG)" $(NGROK_TUNNEL); \
	elif [ -f "$(NGROK_GLOBAL_CONFIG)" ]; then \
		$(NGROK) start --config "$(NGROK_GLOBAL_CONFIG)" --config "$(NGROK_CONFIG)" $(NGROK_TUNNEL); \
	else echo "run: make ngrok-setup"; exit 1; fi

ngrok-tunnel-ephemeral: ## Ephemeral ngrok URL (no reserved domain in ngrok.yml)
	@test -x "$(NGROK)" || (echo "run: make ngrok-install"; exit 1)
	@echo "Mobile: open the root URL exactly (trailing / only). Tap Visit Site if shown."
	@PORT=$${PORT:-$(NGROK_PORT)}; \
	if [ -f "$(NGROK_LOCAL_CONFIG)" ]; then \
		$(NGROK) http --config "$(NGROK_LOCAL_CONFIG)" $$PORT; \
	else $(NGROK) http --config "$(NGROK_GLOBAL_CONFIG)" $$PORT; fi

ngrok-url: ## Print active ngrok HTTPS URL (or reserved domain from ngrok.yml)
	@curl -sf $(NGROK_API)/api/tunnels 2>/dev/null \
	| python3 -c "import sys,json; d=json.load(sys.stdin); t=next((x for x in d.get('tunnels',[]) if x.get('public_url','').startswith('https')), None); print(t['public_url'] if t else '$(NGROK_URL)')" \
	|| echo "$(NGROK_URL)"

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
