.PHONY: test check test-browser demo export

test:
	python3 -m unittest discover -s tests -v

check: test
	python3 scripts/check.py
	node --check app/static/panel.js
	node --check demo/demo.js
	bash -n scripts/deploy.sh

test-browser:
	npm run test:browser
	npm run test:demo

demo:
	python3 scripts/build-demo.py

export:
	python3 scripts/export-public.py
