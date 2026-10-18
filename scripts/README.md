# Scripts

One-off and utility scripts that are worth committing but are not part of the
importable library: data downloads, plotting, migrations, report generation,
experiment drivers.

Nothing here is packaged or installed — `[tool.setuptools]` only picks up
`src/`, so these files never ship in the wheel. They are excluded from the
`mypy` run and from coverage for the same reason.

Run them with uv, which resolves the project environment without needing an
activated virtualenv:

```bash
uv run scripts/my_script.py
```

If a script needs a dependency the project itself does not, add it for that one
invocation with `--with`, which layers it on top of the project environment:

```bash
uv run --with matplotlib scripts/plot_results.py
```

Avoid [PEP 723](https://peps.python.org/pep-0723/) inline metadata (a
`# /// script` block) here. It makes `uv run` resolve an isolated environment
from only the declared dependencies, which means the project itself is *not*
installed and `import janelia_pathlib` fails. Inline metadata
suits standalone scripts that do not touch this package.

Ruff still lints this directory (docstring rules are relaxed — see
`per-file-ignores` in `pyproject.toml`), so `just lint` covers these files.
