.PHONY: dev prod prod-down test lint

dev:
	docker compose up

# Production (homeserver). Named explicitly so compose.override.yaml -- the dev file, which is
# otherwise picked up automatically -- is not merged in.
prod:
	docker compose -f compose.yaml -f compose.prod.yaml up -d --build

prod-down:
	docker compose -f compose.yaml -f compose.prod.yaml down

test:
	cd backend && uv run pytest
	cd frontend && npm test

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run lint
