# Contributing to NYC Lease Lens

Thanks for helping NYC renters spot red flags before they sign. This guide covers setting up, the checks that run on every commit, the commit message format, and how to add a new tool to the agent.

## Setup

You need [uv](https://docs.astral.sh/uv/) and the [gcloud CLI](https://cloud.google.com/sdk/docs/install).

```bash
make setup   # create .env, install deps and git hooks, log in to Google Cloud, enable Vertex AI
make dev     # run the app with auto-reload at http://127.0.0.1:8000
```

`make setup` runs `make install`, which also installs the git hooks described below. If you cloned the repo before the hooks existed, run `make install` once.

Settings live in `.env` (copied from `.env.example`, never committed) and are read by `src/nyc_lease_lens/config.py`. To add a setting, add a field to `Settings` and a line to `.env.example`.

The NYC Open Data tools need no API key, so you can work on them without Google Cloud access. Only chatting with the agent calls Vertex AI.

## Project layout

```
src/nyc_lease_lens/
  app.py          FastAPI routes; wires settings, client, tools and agent together
  agent.py        system prompt and the tool-calling loop
  config.py       Settings (pydantic-settings)
  opendata.py     HTTP client for NYC GeoSearch and Socrata, with retries
  sessions.py     in-memory conversation store
  tools/          one module per tool, registered in tools/__init__.py
scripts/          developer scripts (README table generator)
.github/workflows CI checks and README table updates
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
       description = "One sentence the README shows. Then everything the model needs to use it well."
       parameters = {
           "type": "object",
           "properties": {"bbl": {"type": "string", "description": "10-digit BBL from lookup_building"}},
           "required": ["bbl"],
       }

       def run(self, bbl: str) -> dict[str, Any]: ...
   ```

   `self.client` is the shared `OpenDataClient`. Return a plain dict; the registry turns it into JSON for the model.
2. Add the class to `TOOL_CLASSES` in `tools/__init__.py`.
3. Update `SYSTEM_PROMPT` in `agent.py` if the agent should call it at a specific point.
4. Run `make docs` to add it to the README tables.

### What makes a good tool

These come from problems we hit with NYC Open Data:

- **Return summaries, not raw rows.** A busy building has thousands of records, which would flood the model's context. Aggregate, then add a few examples.
- **Count on the server.** Use SoQL `count(*)` with `$group` for exact numbers. Fetching rows with a `$limit` silently undercounts large buildings.
- **Raise `ToolError` for failures the model should explain** (bad input, service down). Never let an exception escape `run`.
- **Degrade instead of failing.** Socrata response times spike; `OpenDataClient` retries, but if a non-essential query still fails, return what you have with a note, as `get_hpd_violations` does.
- **Explain caveats in a `notes` list** so the model can pass them on: multi-building lots, capped samples, missing data.
- **Watch for renumbered lots.** A lot's BBL can change, and datasets update at different times: 311 keeps complaints under the old BBL, and PLUTO can show 0 units. Use `lot_aliases()` for lot-level queries and the BIN for building-level ones.
- **Validate inputs** such as a 10-digit BBL before building queries from them.

### Addresses to test with

| Address | Why it's useful |
| --- | --- |
| 157 Ludlow St, Manhattan | Small condo; few violations, very high noise complaints |
| 760 Eldert Lane, Brooklyn | Large complex on a renumbered lot; thousands of violations |
| 350 5th Ave (no borough) | Exists in Manhattan and Brooklyn, so lookup must ask |
| 350 5th Ave, Manhattan | Empire State Building; not residential |

### Data sources

| Dataset | ID | Used for |
| --- | --- | --- |
| NYC GeoSearch | (not Socrata) | Address to BBL, BIN and coordinates |
| PLUTO | `64uk-42ks` | Year built, floors, units, owner |
| HPD Buildings | `kj4p-ruqc` | Apartment count by BIN |
| HPD Violations | `wvxf-dwi5` | Housing code violations |
| 311 Service Requests | `erm2-nwe9` | Complaints |

## Logging

Use the standard library logger, never `print` (ruff's `T20` rule rejects it):

```python
logger = logging.getLogger(__name__)
logger.info("check finished", extra={"check": name, "duration_ms": ms_since(started)})
```

- Put values in `extra=` fields rather than in the message. They show up as `key=value` with `LOG_FORMAT=text` and as JSON fields with `LOG_FORMAT=json`. Field names can't reuse `LogRecord` attributes such as `message` or `args` (ruff's `G101` catches this).
- Every line logged during a request carries its request ID. When you run work in threads, use `ContextThreadPoolExecutor` from `nyc_lease_lens.context`, or the ID is lost.
- Levels: `DEBUG` for detail such as chat text, tool arguments and every Open Data query; `INFO` for one line per meaningful step; `WARNING` for slow or failed requests and degraded results; `exception()` for unexpected errors.
- Don't log chat messages or addresses above `DEBUG`.

## Code style

- Ruff decides formatting; don't fight it.
- Type-annotate function signatures; mypy runs on every commit.
- Keep comments sparse: explain *why* something non-obvious is done, not what the code says. Short docstrings on public classes and functions are welcome; module docstrings are not needed.
