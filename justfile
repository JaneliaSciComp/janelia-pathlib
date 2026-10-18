# https://just.systems

# list available recipes
default:
    @just --list

# install the dev environment and pre-commit hooks
install:
    uv sync
    uv run pre-commit install

# run tests with pytest
test:
    uv run pytest

# run tests with pytest and show coverage
test-cov:
    uv run pytest --cov --cov-report=term-missing

# run linting and formatting on all files
lint:
    uv run pre-commit run --all-files

# run type checking (via pre-commit, so stub/type-dep handling matches CI)
typecheck:
    uv run pre-commit run mypy --all-files

# upgrade locked dependency versions (commit the resulting uv.lock)
upgrade:
    uv lock --upgrade

# upgrade a single locked dependency, e.g. `just upgrade-package numpy`
upgrade-package package:
    uv lock --upgrade-package {{ package }}

# build wheel and sdist
build:
    uv build

# tag and release <version>
release version:
    git tag -a {{ version }} -m {{ version }}
    git push origin --follow-tags
