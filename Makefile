.PHONY: up down logs ps lint test prod-pull prod-up prod-ps prod-logs import-osm backup
COMPOSE      = docker compose
PROD_COMPOSE = docker compose -f docker-compose.yml -f docker-compose.prod.yml

up:        ; $(COMPOSE) up -d --build
down:      ; $(COMPOSE) down
logs:      ; $(COMPOSE) logs -f --tail=100
ps:        ; $(COMPOSE) ps

lint:      ; .venv/bin/ruff check server tests
test:      ; .venv/bin/python -m unittest discover -s tests -v   # TEST_DATABASE_URL bazasini TOZALAYDI

# Production (serverda): tayyor image tortiladi, build qilinmaydi
prod-pull: ; $(PROD_COMPOSE) pull
prod-up:   ; $(PROD_COMPOSE) up -d --no-build --remove-orphans
prod-ps:   ; $(PROD_COMPOSE) ps
prod-logs: ; $(PROD_COMPOSE) logs -f --tail=100

# OSM masjidlarini import (osm-mosques.json joriy papkada bo'lishi kerak; docs/IMPORT-OSM.md)
import-osm:
	$(PROD_COMPOSE) run --rm -v "$$PWD/osm-mosques.json:/tmp/osm-mosques.json:ro" web import-osm --file /tmp/osm-mosques.json

# Baza zaxirasi (docs/DEPLOY-DOCKER.md)
backup:
	@mkdir -p backups
	$(COMPOSE) exec -T postgres pg_dump -U $${POSTGRES_USER:-masjid} -Fc $${POSTGRES_DB:-masjid} > backups/masjid-$$(date +%F).dump
	@ls -lh backups | tail -3
