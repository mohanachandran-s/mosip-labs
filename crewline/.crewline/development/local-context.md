# crewline (this repository)

- **Language:** Python 3.11+
- **Install:** `pip install -r validators/requirements.txt`
- **Tests:** from `validators/`: `python -m pytest -q`
- **Coverage:** `pytest --cov=crewline --cov-report=xml` (cobertura `coverage.xml`; `crewline diff-coverage --include '*validators/crewline/*.py'`)
- **Lint:** none required beyond the validators; do not invent a style guide
- **Default approved paths (framework work):** `validators/crewline/**`, `validators/tests/**`, `core/**`, `agents/**`, `adapters/**`, `skills/**`, `evals/**`, `README.md`
- **Pack:** `python -m crewline pack` (default mosip). This repo is the core + packs, not a MOSIP service.
- **No TestRig / JaCoCo** here. System tests of Crewline are `python -m crewline evals --pack frozen --run` and `cold-start`.
