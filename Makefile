.COMPOSE := docker compose
COMPOSE := docker compose

.PHONY: up down ps logs restart urls simulate loadtest metrics

up:            ## start the whole stack (first boot imports n8n workflows)
	$(COMPOSE) up -d --build
	@echo "n8n:  http://localhost:5678  |  Gateway: http://localhost:9090  |  Grafana: http://localhost:3000"

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
	@echo "Gateway API:   http://localhost:9090  (/health /stats /metrics /tickets)"
	@echo "RabbitMQ UI:   http://localhost:15672  (dailyops/dailyops)"
	@echo "Grafana:       http://localhost:3000  (admin/admin)"
	@echo "Prometheus:    http://localhost:9091"
	@echo "Mock K8s:      http://localhost:9100"

simulate:      ## e.g. make simulate S=happy   (scenarios: happy dedup concurrency latency failure poison all)
	$(COMPOSE) run --rm simulator node simulator.js $(S)

loadtest:      ## e.g. make loadtest E=200 R=25
	$(COMPOSE) run --rm simulator node loadtest.js --events $(or $(E),100) --rate $(or $(R),25)

metrics:
	@curl -s http://localhost:9090/metrics | grep '^dailyops'
