.PHONY: run test up down logs

run:
	UDM_DATA_DIR=./data UDM_DOWNLOAD_DIR=./downloads uvicorn app.main:app --reload --port 8080

test:
	python -m pytest -q

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f crate
