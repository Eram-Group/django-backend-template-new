# Linting

How Ruff's rule set is chosen here, and why the config looks the way it does.
The config itself lives in `pyproject.toml` under `[tool.ruff]`.

## How rule selection works

Ruff ships ~968 rules. Each has a code — `E501`, `S101`, `TRY400` — whose
letter prefix is its family, almost always a flake8 plugin Ruff reimplemented
(`S` = bandit, `PTH` = use-pathlib, `DTZ` = datetimez).

`lint.select` is the allowlist. Two properties matter:

1. **It replaces Ruff's defaults — it is not additive.** The moment `select`
   is defined, Ruff's own default set is discarded and only our list applies.
2. **It selects by prefix.** `"S"` enables every `S###`; `"TID25"` enables
   only `TID251`/`TID252`.

Property 1 is a real trap. Ruff 0.16 grew its default set from 59 rules to
413 and **nothing changed here**, because the defaults had not been in play
since the day the list was written. Staying current means diffing the two
sets deliberately, not reading release notes:

```bash
# what Ruff enables by default vs. what we enable
uv run ruff check --show-settings . | sed -n '/^linter.rules.enabled = \[/,/^\]/p'
```

## Why `ignore` exists when `select` is an allowlist

Because `select` is coarse. Selecting a family pulls in rules you did not
individually choose, and `ignore` subtracts them back out:

| `select` entry | why we want it | `ignore` removes |
|---|---|---|
| `"TRY"` | `TRY400` — `.error()` where `.exception()` belongs | `TRY003` |
| `"PLC"` | pylint conventions | `PLC0415` |
| `"RUF"` | Ruff-specific rules | `RUF012` |

Each carve-out is a framework contract, not a style preference — `TRY003`
fights the `ApplicationError` idiom (the message is passed inline; the one
envelope is built in `config/api/exception_handlers.py`), `PLC0415` fights
the deliberate function-level imports, `RUF012` fights Django's
class-attribute idiom (`Meta`, admin `list_display`).

Avoiding `ignore` entirely would mean listing individual rule codes instead
of prefixes. Nobody does this: hundreds of hand-maintained codes, and no new
rules ever arrive on upgrade.

## Suppression scopes

Three mechanisms, same job, different granularity. **Use the narrowest one
that works.**

| Scope | Mechanism | Example here |
|---|---|---|
| Repo-wide | `lint.ignore` | `RUF012`, `TRY003`, `PLC0415` |
| By path | `lint.per-file-ignores` | `S101` under `**/tests/**` — `assert` is the point there |
| One line | `# noqa: <CODE>` | `PLW0603` on the process-wide httpx pool |

`PLW0603` is the worked example: one deliberate `global` in
`apps/common/http.py`, exempted at that line so `PLW` still catches a stray
`global` anywhere else. A repo-wide ignore would have thrown the rule away
for one site.

`RUF100` (unused-noqa) is enabled, so a `noqa` that stops being needed is
flagged rather than left to rot. `PGH` is enabled too, so every `noqa` and
`type: ignore` must name its code — no blanket suppressions.

## Ruff's recommendations, and where we stand

Quoted from [Ruff's linter docs](https://docs.astral.sh/ruff/linter/):

> "Prefer `lint.select` over `lint.extend-select` to make your rule set explicit."
>
> "Start with a small set of rules (`select = ["E", "F"]`) and add a category at-a-time."
>
> "Use `ALL` with discretion. Enabling `ALL` will implicitly enable new rules whenever you upgrade."

For suppression Ruff recommends the hierarchy above — config, then path,
then inline — preferring explicit narrow suppressions over blanket ones.

This repo follows all of it: an explicit `select`, categories adopted a
batch at a time (each verified clean at adoption, so the gate never went
red), and three repo-wide ignores against three path-scoped and one
line-level suppression.

## Adding a category

Measure before adopting. `--extend-select` probes a candidate without
touching the config, and **takes precedence over `lint.ignore`** — so it
shows everything the family would surface, including rules we already
ignore:

```bash
uv run ruff check . --extend-select TRY --statistics
# 4  TRY003  raise-vanilla-args   <- reported even though TRY003 is ignored
```

Adopt when the count is zero or the findings are worth fixing. If a family
is valuable but one rule inside it fights a project idiom, take the family
and carve out that rule — that is how `TRY`, `PLC` and `RUF` are configured.

**Gotcha:** an inline `--config 'lint.ignore=[...]'` override silently drops
`lint.select` from the file, so the run reports far too little. Pass both
together:

```bash
uv run ruff check . --config 'lint.select=["ALL"]' --config 'lint.ignore=["D203"]'
```

## Deliberately not enabled

Recorded so the decision is not relitigated every upgrade. Counts are
findings at the time of the audit.

| Family | Count | Why not |
|---|---|---|
| `TC` | 143 | Would move imports into `TYPE_CHECKING`. **Breaks pydantic and django-ninja**, which read annotations at runtime. |
| `ARG` | 69 | Unused args are signature contracts we do not control: Django's `request`, ninja error handlers' `exc`, pytest's dependency-only `db`. |
| `SLF` | 25 | `Model._meta` is Django's de-facto public API despite the underscore. |
| `PLR` | 101 | Magic values (seed weights, status codes) and complexity counters the flat-service style rejects. |
| `D` | 628 | Mandatory docstrings — a real policy decision, still open. |
| `CPY` | 260 | No per-file copyright headers; `LICENSE` sits at the repo root. |
| `ANN` | 100 | Redundant: mypy strict already fails on missing annotations. |
| `EM`, `FBT` | 4, 4 | Inline exception messages and the admin's boolean capability API. |
| `C90` | 2 | Needs a complexity threshold — a style decision, not a fix. |
| `TD`, `FIX` | 0 | Zero findings, but adopting them sets policy on inline TODOs; work is tracked in `TODO.json`. |

`ALL` is not used, per Ruff's own advice. Running it manually is still a
useful audit — it is how `LOG004` caught a real bug in the refund path:

```bash
uv run ruff check . --select ALL --statistics
```

## Keeping ruff in lockstep

Ruff is pinned twice: as a dev dependency in `uv.lock`, and as a hook rev in
`.pre-commit-config.yaml`. **Both must move together** — CI runs
`pre-commit run --all-files` as the single lint source, so a stale hook rev
silently diverges from the local `just lint`. Dependabot's `uv` and
`pre-commit` ecosystems each propose their half; land them in one change.
