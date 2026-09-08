.COMPOSE := docker compose
COMPOSE := docker compose

.PHONY: up down ps logs restart urls migrate migrate-check decide-health

up:            ## start the whole stack (first boot imports n8n workflows)
	$(COMPOSE) up -d --build
	@echo "n8n:  http://localhost:5678  |  Agent: http://localhost:8000  |  Grafana: http://localhost:3001"

down:          ## stop everything
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100

restart-n8n:
	$(COMPOSE) restart n8n

urls:
	@echo "n8n UI:        http://localhost:5678"
	@echo "Agent API:     http://localhost:8000  (/health /agent/decide)"
	@echo "Postgres:      localhost:5435  (dailyops/dailyops)"
	@echo "Grafana:       http://localhost:3001  (admin/admin)"
	@echo "Prometheus:    http://localhost:9091"

# OPS-1: apply DB migrations (001..005, idempotent) to the running postgres.
# Starts postgres if needed, then applies schema.sql + migrations in order.
MIGRATIONS := $(wildcard db/migrations/*.sql)

migrate:  ## apply all DB migrations in order (idempotent, safe to re-run)
	@docker compose up -d postgres >/dev/null && \
	for i in 1 2 3 4 5 6 7 8 9 10; do \
	  docker exec dailyops-postgres pg_isready -U dailyops >/dev/null 2>&1 && break; sleep 1; \
	done
	@echo "--- applying db/schema.sql (no-op where tables exist) ---"; \
	docker exec -i dailyops-postgres psql -U dailyops -d dailyops -v ON_ERROR_STOP=1 < db/schema.sql
	@for f in $(sort $(MIGRATIONS)); do \
	  echo "--- applying $$f ---"; \
	  docker exec -i dailyops-postgres psql -U dailyops -d dailyops -v ON_ERROR_STOP=1 < "$$f" || exit 1; \
	done
	@echo "DATABASE MIGRATIONS OK"

migrate-check: ## print current approvals/action_executions schema (post-migration sanity check)
	@docker exec -i dailyops-postgres psql -U dailyops -d dailyops -c "\d approvals" -c "\d action_executions"

decide-health: ## smoke-test the agent-service contract
	@curl -s http://localhost:8000/health
	@echo
	@curl -s -X POST http://localhost:8000/agent/decide \
	  -H 'content-type: application/json' \
	  $(if $(AGENT_API_KEY),-H 'X-API-Key: $(AGENT_API_KEY)',) \
	  -d '{"correlation_id":"run_smoke_001","event":{"type":"inventory_check","entity":"product_A","payload":{"stock":15,"avg_daily_sales":8}},"context":{"history_window_days":30,"requested_by":"daily-report-workflow"}}'
	@echo
