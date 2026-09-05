.COMPOSE := docker compose
COMPOSE := docker compose

.PHONY: up down ps logs restart urls decide-health

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

decide-health: ## smoke-test the agent-service contract
	@curl -s http://localhost:8000/health
	@echo
	@curl -s -X POST http://localhost:8000/agent/decide \
	  -H 'content-type: application/json' \
	  -d '{"correlation_id":"run_smoke_001","event":{"type":"inventory_check","entity":"product_A","payload":{"stock":15,"avg_daily_sales":8}},"context":{"history_window_days":30,"requested_by":"daily-report-workflow"}}'
	@echo
