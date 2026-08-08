.PHONY: up down logs rebuild seed enrich stats tools clean

up:            ## build and start the whole pipeline
	docker compose up --build -d
	@echo ""
	@echo "  UI    http://localhost:$${UI_PORT:-8080}"
	@echo "  API   http://localhost:$${API_PORT:-8000}/docs"
	@echo ""

down:          ## stop everything (keeps the database)
	docker compose down

logs:          ## tail all logs
	docker compose logs -f --tail=50

rebuild:       ## force a clean rebuild of all images
	docker compose build --no-cache

seed:          ## re-run the fixture scraper (safe, it dedupes)
	docker compose run --rm scraper-fixtures

enrich:        ## run every enrichment job once, now
	docker compose run --rm enrichment python run.py --once

stats:         ## what's in the store right now
	@curl -s http://localhost:$${API_PORT:-8000}/stats | python3 -m json.tool

tools:         ## start mongo-express on :8081
	docker compose --profile tools up -d mongo-express

clean:         ## stop everything and delete the database volume
	docker compose down -v
