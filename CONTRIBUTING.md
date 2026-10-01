# Contributing to NYC Lease Lens

Thanks for helping NYC renters spot red flags before they sign. This guide covers setting up, the checks that run on every commit, the commit message format, and how to add a new tool to the agent.

## Setup

You need [uv](https://docs.astral.sh/uv/) and the [gcloud CLI](https://cloud.google.com/sdk/docs/install).

```bash
make setup   # create .env, install deps and git hooks, log in to Google Cloud, enable Vertex AI
make dev     # run the app with auto-reload at http://127.0.0.1:8000
```

`make setup` runs `make install`, which also installs the git hooks described below. If you cloned the repo before the hooks existed, run `make install` once.

Settings live in `.env` (copied from `.env.example`, never committed) and are read by `src/nyc_lease_lens/config.py`, in groups: `settings.llm`, `settings.server`, `settings.opendata` (variables prefixed `OPENDATA_`) and `settings.logging` (prefixed `LOG_`). To add a setting, add a field to the matching group and a line to that group's section of `.env.example`.

Only runtime choices belong in settings (timeouts, retries, the model, log level). Values that change what a grade means stay in code so they're reviewed and tested: look-back periods, the neighbor radius, heating seasons and sample sizes in `src/nyc_lease_lens/rules.py`, and scoring thresholds in `scoring/red_flags.py`. For a tool parameter with limits, add a `Range` to `rules.py` and use its `.schema()`, `.default` and `.clamp()`, so the limits the model sees always match the ones enforced.

The NYC Open Data tools need no API key, so you can work on them without Google Cloud access. Only chatting with the agent calls Vertex AI.

## Project layout

```
src/nyc_lease_lens/
  __main__.py      starts uvicorn with create_app
  config.py        runtime settings, by group (pydantic-settings)
  rules.py         domain rules: look-back periods, radius, heating seasons, sample sizes
  api/             create_app() and lifespan (app.py), routes and dependencies, request-ID middleware, schemas
  agent/           the tool-calling loop (loop.py), system prompt (prompts.py), conversation store
  tools/           one module per tool, registered in tools/__init__.py
  scoring/         red flags and good signs (red_flags.py), the rule types and how they're applied (engine.py)
  data/            Open Data client, dataset registry, SoQL and parsing helpers
  observability/   logging setup and the request-ID context
  static/          the chat page and logo
scripts/           developer scripts (README table generator)
.github/workflows  CI checks and README table updates
```

Importing a module has no side effects: nothing reads settings or builds clients until `create_app()` runs. In tests, build an app with fakes in one line and use FastAPI's `TestClient` (the `with` block runs startup and shutdown):

```python
app = create_app(Settings(), agent=FakeAgent())
with TestClient(app) as http:
    assert http.post("/chat", json={"message": "hi"}).status_code == 200
```

## Checks on every commit

Git hooks run automatically when you commit. You can run them all yourself with `make check`.

| Hook | What it does | Fix it with |
| --- | --- | --- |
| ruff check | Lint (pyflakes, pycodestyle, isort, bugbear, pyupgrade) | `make format`, then fix what remains |
| ruff format | Code formatting, 120-character lines | `make format` |
| mypy | Type checking of `src/` and `scripts/` | `make typecheck` to see the errors |
| README tables | Regenerates the make targets and tools tables | `make docs` |
| File checks | Trailing whitespace, final newline, valid YAML/TOML, merge markers, large files, private keys | Most fix themselves |
| Commit message | Enforces Conventional Commits (see below) | Reword the message |

When a hook fixes files for you (formatting, whitespace, README tables), the commit stops so you can review the changes. Run `git add` and commit again.

Avoid `git commit --no-verify`: CI runs the same checks and will fail anyway.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary`, in the imperative, lowercase, no trailing period.

```
feat(tools): add get_311_complaints tool
fix(opendata): retry slow Socrata requests
docs(readme): add quickstart using make targets
```

| Type | Use for |
| --- | --- |
| `feat` | A new capability, such as a tool or an agent behavior |
| `fix` | A bug fix |
| `refactor` | Code changes that don't change behavior |
| `perf` | Speed improvements |
| `test` | Adding or fixing tests |
| `docs` | README, CONTRIBUTING, docstrings |
| `build` | Dependencies, `pyproject.toml`, makefile, dev tooling |
| `ci` | GitHub workflows |
| `chore` | Anything else, such as `.gitignore` or config housekeeping |
| `style` | Formatting-only changes |
| `revert` | Reverting an earlier commit |

The scope is optional. Scopes used so far: `tools`, `app`, `config`, `opendata`, `readme`, `docs`, `make`, `dev`, `project`, `python`, `env`. Add `!` after the type or scope for breaking changes, e.g. `feat(tools)!: rename bbl parameter`.

Keep one logical change per commit. If a commit needs "and" in its summary, it can probably be split.

## Continuous integration

Every push and pull request runs `.github/workflows/ci.yml`:

- **Lint, format and type check** runs the same hooks as your local commit.
- **Conventional commit messages** checks every new commit message.

`.github/workflows/readme-docs.yml` regenerates the README tables when tools, the makefile or the README change, and pushes a `docs(readme): update generated tables` commit if they were out of date. If that happens, `git pull` before your next push. Running `make docs` before committing avoids it.

## Adding a tool

Tools are how the agent gets facts. Each one is a class in its own module under `src/nyc_lease_lens/tools/`.

1. Create `tools/<name>.py` with a `Tool` subclass:

   ```python
   class GetSomething(Tool):
       name = "get_something"
       error_label = "Something lookup"  # failed requests become "Something lookup failed: ..."
       data_sources = (datasets.SOMETHING,)
       description = "One sentence the README shows. Then everything the model needs to use it well."
       parameters = {
           "type": "object",
           "properties": {"bbl": {"type": "string", "description": "10-digit BBL from lookup_building"}},
           "required": ["bbl"],
       }

       def run(self, bbl: str) -> dict[str, Any]:
           bbl = self.validate_bbl(bbl)
           rows = self.query(datasets.SOMETHING, {"$where": f"bbl='{bbl}'", "$select": "count(*) AS n"})
           return {"bbl": bbl, "count": to_int(rows[0]["n"]) if rows else 0}
   ```

   The base class provides what every tool needs:
   - `self.query(dataset, params)` runs a SoQL query, and `self.query_in(dataset, field, values, params)` filters on a list of any length. Both turn a failed request into a `ToolError`. For the other helpers in `tools/building.py`, such as `lot_aliases`, use `self.fetch(lot_aliases, self.client, bbl)`.
   - `self.validate_bbl()` / `self.validate_bin()` check IDs before they go into a query.
   - Build query fragments with `nyc_lease_lens.data.soql` (`quote`, `in_list`) and parse values with `nyc_lease_lens.data.parsing` (`to_int`, `to_date`) rather than by hand.

   Return a plain dict; the registry turns it into JSON for the model.
2. List the datasets it reads in `data_sources` (see [Data sources](#data-sources) below).
3. Add the class to `TOOL_CLASSES` in `tools/__init__.py`.
4. Update `SYSTEM_PROMPT` in `agent/prompts.py` if the agent should call it at a specific point.
5. Run `make docs` to add it to the README tables.

### What makes a good tool

These come from problems we hit with NYC Open Data:

- **Return summaries, not raw rows.** A busy building has thousands of records, which would flood the model's context. Aggregate, then add a few examples.
- **Count on the server.** Use SoQL `count(*)` with `$group` for exact numbers. Fetching rows with a `$limit` silently undercounts large buildings.
- **Raise `ToolError` for failures the model should explain** (bad input, service down). Never let an exception escape `run`.
- **Degrade instead of failing.** Socrata response times spike; `OpenDataClient` retries, but if a non-essential query still fails, return what you have with a note, as `get_hpd_violations` does.
- **Explain caveats in a `notes` list** so the model can pass them on: multi-building lots, capped samples, missing data.
- **Watch for renumbered lots.** A lot's BBL can change, and datasets update at different times: 311 keeps complaints under the old BBL, and PLUTO can show 0 units. Use `lot_aliases()` for lot-level queries and the BIN for building-level ones.
- **Validate inputs** such as a 10-digit BBL before building queries from them (`validate_bbl`, `validate_bin`), and quote strings with `soql.quote`.

### Addresses to test with

| Address | Why it's useful |
| --- | --- |
| 157 Ludlow St, Manhattan | Small condo; few violations, very high noise complaints |
| 760 Eldert Lane, Brooklyn | Large complex on a renumbered lot; thousands of violations |
| 350 5th Ave (no borough) | Exists in Manhattan and Brooklyn, so lookup must ask |
| 350 5th Ave, Manhattan | Empire State Building; not residential |

### Data sources

Every dataset is defined once in `src/nyc_lease_lens/data/datasets.py`, and the README's Data table is generated from it. To use a new dataset, add a `Dataset` there (with its official name and publisher from the dataset's page on data.cityofnewyork.us) and to `ALL`, then query it by `datasets.YOUR_DATASET.id`.

## Testing

`make test` runs the suite offline in a few seconds; CI runs it on every push.

| File | Covers |
| --- | --- |
| `test_tools_golden.py` | Every tool on edge-case buildings, against saved expected outputs |
| `test_scoring.py` | Each red flag at its thresholds, caps, grade bands, good signs |
| `test_tool_helpers.py` | Violation and complaint categories, citation cleanup, heating seasons, bedbug deadlines |
| `test_agent_loop.py` | The tool-calling loop, with a fake model |
| `test_api.py` | Routes, sessions, request IDs, error handling, through `create_app` with a fake agent |
| `test_registry.py`, `test_config.py`, `test_rules.py`, `test_data_helpers.py` | The tool registry, settings, domain rules, SoQL and parsing helpers |

**How the tool tests stay offline.** `tests/fixtures/opendata.json.gz` holds real Open Data responses, recorded once. A `ReplayClient` serves them instead of the network, and any query that wasn't recorded fails the test, so an accidental change to a query shows up immediately. Tools build their queries from today's date, so these tests freeze the clock at the recording date (`on_recording_day`).

**When a tool's output changes on purpose,** regenerate the expected outputs and review the diff before committing:

```bash
UPDATE_GOLDEN=1 uv run pytest tests/test_tools_golden.py
git diff tests/fixtures/golden_tools.json
```

**When a tool sends a new query** (or you add a golden case), the recorded responses need it too. Re-record every case from the live APIs, which takes a few minutes and saves the recording date with the responses, then update the golden outputs. Live data changes daily, so expect the golden diff to include real changes as well as yours:

```bash
uv run python scripts/record_opendata.py
UPDATE_GOLDEN=1 uv run pytest tests/test_tools_golden.py
```

**Warnings are errors** in tests, so a new deprecation gets fixed rather than ignored.

## Changing the score

Every red flag is a `Rule` in `RULES` in `src/nyc_lease_lens/scoring/red_flags.py`; the types and the code that applies them are in `scoring/engine.py`. A rule has:
- **what it measures**, and the source it comes from
- **a `find` function** that reads the check results and returns `Hit`s: a value plus the text shown to the user
- **`tiers` of `(at least, points)`**, highest first
- optionally **`count_tiers`** (combined by `lower` or `higher`) and a **`cap`** on the rule's total points

Good signs are listed separately in `GOOD_SIGNS`.

- **To change a weight,** edit the rule's tiers. `make docs` updates the README's weights table.
- **To add a red flag,** write a small `find` function and add a `Rule`. Its position in `RULES` decides the order of findings that tie on points.
- Pass `today=` to `score()` in tests, so the rules about recent years give the same result every time.

Changing a weight changes grades users see, so mention the change and why in the commit message.

## Logging

Use the standard library logger, never `print` (ruff's `T20` rule rejects it):

```python
logger = logging.getLogger(__name__)
logger.info("check finished", extra={"check": name, "duration_ms": ms_since(started)})
```

- Put values in `extra=` fields rather than in the message. They show up as `key=value` with `LOG_FORMAT=text` and as JSON fields with `LOG_FORMAT=json`. Field names can't reuse `LogRecord` attributes such as `message` or `args` (ruff's `G101` catches this).
- Every line logged during a request carries its request ID. When you run work in threads, use `ContextThreadPoolExecutor` from `nyc_lease_lens.observability.context`, or the ID is lost.
- Levels: `DEBUG` for detail such as chat text, tool arguments and every Open Data query; `INFO` for one line per meaningful step; `WARNING` for slow or failed requests and degraded results; `exception()` for unexpected errors.
- Don't log chat messages or addresses above `DEBUG`.

## Code style

- Ruff decides formatting; don't fight it.
- Type-annotate function signatures; mypy runs on every commit.
- Keep comments sparse: explain *why* something non-obvious is done, not what the code says. Short docstrings on public classes and functions are welcome; module docstrings are not needed.
