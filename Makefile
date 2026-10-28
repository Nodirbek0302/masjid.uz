.PHONY: lint test migrate serve
lint:    ; .venv/bin/ruff check server tests
test:    ; .venv/bin/python -m unittest discover -s tests -v   # WIPES schema masjiduz in TEST_DATABASE_URL
migrate: ; .venv/bin/python server/cli.py migrate
serve:   ; .venv/bin/python server/cli.py serve
