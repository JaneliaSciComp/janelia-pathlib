"""Report dependencies whose newest release is excluded by its declared bound.

Dependabot keeps `uv.lock` current, but only within the constraints written in
`pyproject.toml`: a dependency capped at `<3` is never offered a 3.x update.
This script finds exactly the complement of that -- releases that exist but are
forbidden by the declared specifier -- so the resulting issue never duplicates
what is already sitting in a Dependabot pull request.

Writes a markdown bullet list to stdout; empty output means nothing to report.
"""

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

# "name v1.2.3 (latest: v4.5.6)", with the tree-drawing prefix already stripped.
# Dependency-group entries carry an extra "(group: dev)" segment between the
# version and "(latest:", so anything parenthesised in between is skipped.
TREE_LINE = re.compile(
    r"^\s*[^a-zA-Z0-9]*([A-Za-z0-9._-]+)\s+v(\S+)(?:\s+\([^)]*\))*?\s+\(latest:\s+v(\S+)\)"
)


def declared_specifiers() -> dict[str, SpecifierSet]:
    """Map normalized distribution name -> declared specifier from pyproject.toml."""
    data = tomllib.loads(Path("pyproject.toml").read_text())
    specifiers: dict[str, SpecifierSet] = {}

    groups = [data.get("project", {}).get("dependencies", [])]
    groups.extend(data.get("project", {}).get("optional-dependencies", {}).values())
    # PEP 735 groups (test/docs/dev) carry bounds too, so they are reported on
    # the same footing as runtime dependencies.
    groups.extend(data.get("dependency-groups", {}).values())

    for entries in groups:
        for entry in entries:
            # `{ include-group = "test" }` entries are dicts, not requirement
            # strings; the included group is visited on its own anyway.
            if not isinstance(entry, str):
                continue
            try:
                requirement = Requirement(entry)
            except (InvalidSpecifier, ValueError):
                continue
            specifiers[canonical(requirement.name)] = requirement.specifier

    return specifiers


def canonical(name: str) -> str:
    """PEP 503 name normalization, so `Foo_Bar` and `foo-bar` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def outdated_entries() -> list[tuple[str, str, str]]:
    """Return (name, resolved, latest) for direct deps with a newer release."""
    # check=True: a failed resolve must surface as a red workflow run rather
    # than as an empty report that looks like "nothing to update".
    # No --no-dev: dev/test/docs bounds hold back releases just as runtime ones
    # do, and those are what this report exists to surface.
    result = subprocess.run(
        ["uv", "tree", "--outdated", "--depth", "1"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "NO_COLOR": "1"},  # NO_COLOR keeps ANSI escapes out
    )

    entries = []
    for line in result.stdout.splitlines():
        match = TREE_LINE.match(line)
        if match:
            entries.append((match.group(1), match.group(2), match.group(3)))
    return entries


def main() -> None:
    specifiers = declared_specifiers()
    blocked = []

    for name, resolved, latest in outdated_entries():
        specifier = specifiers.get(canonical(name))

        # Unknown, or declared with no constraint at all: Dependabot can reach
        # the new version by itself, so it is not this report's business.
        if specifier is None or not str(specifier):
            continue

        try:
            # prereleases=True so that a bound excluding only a prerelease is
            # not mistaken for one blocking a real release.
            is_allowed = specifier.contains(Version(latest), prereleases=True)
        except InvalidVersion:
            continue

        # If the newest release satisfies the declared bound, Dependabot can
        # reach it on its own and it does not belong in this report.
        if not is_allowed:
            blocked.append((name, resolved, latest, str(specifier)))

    for name, resolved, latest, specifier in sorted(blocked):
        print(f"- `{name}` {resolved} -> **{latest}**, held back by `{name}{specifier}`")


if __name__ == "__main__":
    sys.exit(main())
