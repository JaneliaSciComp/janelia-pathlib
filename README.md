# janelia-pathlib

[![CI](https://github.com/JaneliaSciComp/janelia_pathlib/actions/workflows/ci.yaml/badge.svg)](https://github.com/JaneliaSciComp/janelia_pathlib/actions/workflows/ci.yaml)
[![codecov](https://codecov.io/gh/JaneliaSciComp/janelia_pathlib/branch/main/graph/badge.svg)](https://codecov.io/gh/JaneliaSciComp/janelia_pathlib)
[![PyPI](https://img.shields.io/pypi/v/janelia-pathlib.svg)](https://pypi.org/project/janelia-pathlib)

Translate Janelia file share paths to and from an unambiguous `janelia:` URI, so code can refer to a share without depending on OS-specific mount guesses.

Janelia file shares are mounted at different paths on Linux, Mac, and Windows, and on macOS two different shares can even collide on the same guessed mount point (e.g. `/Volumes/<name>`). This package gives you two functions to move between a local path and a stable identity that's never ambiguous.

## Installation

```bash
uv add janelia-pathlib
```

Or, to track the latest unreleased commit:

```bash
uv add "janelia-pathlib @ git+https://github.com/JaneliaSciComp/janelia_pathlib"
```

Requires Python 3.11+. Zero runtime dependencies beyond `platformdirs`.

## Quick start

```python
from janelia_pathlib import to_janelia_uri, from_janelia_uri

# Canonicalize a local path to its unambiguous janelia: identity
uri = to_janelia_uri("/labs/alpha/alphalab/sub/data")
# "janelia:/labs/alpha/alphalab/sub/data"

# Resolve a janelia: URI back to a real, usable local path
path = from_janelia_uri(uri)
# "/Volumes/alphalab/sub/data" (on Mac), "/labs/alpha/alphalab/sub/data" (on Linux), ...
```

## Why `janelia:`?

A share's local mount point differs by OS, and on macOS it isn't even guaranteed to be unique: two shares with the same share name on different servers (e.g. `host-a.example.org/data` and `host-b.example.org/data`) both nominally want `/Volumes/data` — macOS mounts the second at `/Volumes/data-1` instead. A bare path string like `/Volumes/data` can't tell you which share that actually is.

`janelia:/<linux_path>` uses the share's Linux path as its identity. Linux mount points are fixed and admin-assigned, so they're unique and stable — unlike a macOS mountpoint name or a Windows drive letter, and unlike the SMB host, which changes if a share migrates to new hardware. The URI is safe to store, log, or pass between machines, and always resolves back to the *correct* share.

### Why one slash, not two?

`//` in a URI opens an [authority component](https://datatracker.ietf.org/doc/html/rfc3986#section-3.2) — the `host` in `https://host/path`. A Linux path has nothing to put there: a share's identity spans a variable number of leading segments (`/labs/alpha/home`, `/nrs/foxtrot`) and is only recoverable by prefix-matching the share table, so there's no fixed cut point. `janelia://labs/alpha/...` would silently parse `labs` — a storage tier — as the authority. The single-slash form says there is no authority, the same way `mailto:` and `urn:` do.

## API

### `to_janelia_uri(path)`

Translate a path to its canonical `janelia:/<linux_path>` form.

Three input formats are **self-describing** and resolve on any OS, regardless of the machine you're running on:

| Format | Example |
|---|---|
| Linux path | `/labs/alpha/alphalab/data` |
| Windows UNC | `\\fileserver.example.org\alphalab\data` |
| SMB URL | `smb://fileserver.example.org/alphalab/data` |

```python
from janelia_pathlib import to_janelia_uri

# All three produce the same identity, on any OS:
to_janelia_uri("/labs/alpha/alphalab/data")
to_janelia_uri("smb://fileserver.example.org/alphalab/data")
# "janelia:/labs/alpha/alphalab/data"
```

Two formats are **inherently OS-local** and only resolve while running on that OS, with the share mounted:

| Format | Example | Why |
|---|---|---|
| macOS mountpoint | `/Volumes/alphalab/data` | The name is assigned from the share name alone, so two shares can collide (`/Volumes/data` vs `/Volumes/data-1`). Only the live `mount` table says which is which. |
| Windows drive letter | `Z:\data` | Per-user mapping state; the letter carries no information about the share. |

For these, the live mount table is consulted rather than guessing from `mac_path`/`windows_path` — a guess could be wrong in exactly the way the mount-name collision causes. If the share isn't mounted, the path passes through unchanged; use `from_janelia_uri` first if you need to mount it.

Paths that don't match any known share are returned unchanged:

```python
to_janelia_uri("/tmp/local/scratch/file.txt")
# "/tmp/local/scratch/file.txt"
```

### `from_janelia_uri(path_str, *, timeout=None)`

Translate a `janelia:/<linux_path>` string to a real, usable local path.

```python
from janelia_pathlib import from_janelia_uri

from_janelia_uri("janelia:/labs/alpha/alphalab/data")
# "/Volumes/alphalab/data" (on Mac)
# "/labs/alpha/alphalab/data" (on Linux)
```

On **macOS**, if the share isn't already mounted, this mounts it on demand (triggering the native SMB mount, which may prompt for credentials) and waits up to `timeout` seconds for the mount to appear, returning its *real* local mountpoint — not a guessed `/Volumes/<share>` path, which may not exist yet or may belong to a different, colliding share. `timeout` defaults to `JANELIA_PATHLIB_MOUNT_TIMEOUT` (30s). Raises `RuntimeError` if the URI doesn't match a known share, or if mounting fails or times out.

On **Linux** and **Windows**, no mounting step is needed — shares are already available at a fixed path (Linux) or accessible directly via UNC path (Windows).

A string that isn't a `janelia:` URI is returned unchanged:

```python
from_janelia_uri("/tmp/local/scratch/file.txt")
# "/tmp/local/scratch/file.txt"
```

Both functions round-trip: `from_janelia_uri(to_janelia_uri(p))` resolves back to a valid local path for `p`'s share, and non-Janelia paths pass through both functions untouched.

### `janelia_path(path_str)`

An [`argparse`](https://docs.python.org/3/library/argparse.html) `type=` converter, for scripts that take a path on the command line:

```python
from janelia_pathlib import janelia_path

parser.add_argument("--data", type=janelia_path)
```

```console
$ my-script --data janelia:/labs/alpha/alphalab/data
```

The script receives `args.data` already converted to a local path, so it never has to care which OS it's on. Ordinary paths still work — anything that isn't a `janelia:` URI is passed through — so this is safe to add to an existing argument.

Failures become normal argparse errors rather than tracebacks:

```console
$ my-script --data janelia:/labs/nope/data
demo: error: argument --data: No known share for URI: janelia:/labs/nope/data
```

On macOS this mounts the share during `parse_args()`, which may block on a credential prompt. Set [`JANELIA_PATHLIB_NO_MOUNT`](#never-mounting) when nobody is there to answer.

## Share data

Share data is **not** bundled with the package — it's fetched from the [Fileglancer API](https://fileglancer.int.janelia.org/api/file-share-paths) and cached in your user cache directory:

- **macOS**: `~/Library/Caches/janelia-pathlib/shares.json`
- **Linux**: `~/.cache/janelia-pathlib/shares.json`
- **Windows**: `%LOCALAPPDATA%\janelia-pathlib\Cache\shares.json`

**Every time** `to_janelia_uri`/`from_janelia_uri` first needs share data in a process, the library tries to refresh this cache from the API (once per process — subsequent calls in the same process reuse that result, so a long-running process won't pick up newly added shares until restarted). This requires access to the Janelia network, but a failed refresh never breaks translation outright:

- If the refresh fails but a cache file already exists, the **existing (possibly stale) cache is used instead**, with a `UserWarning`.
- If the refresh fails and there's **no cache file at all**, share data degrades to "no known shares" (also with a warning) — paths just pass through untranslated rather than raising.

To force a refresh manually (e.g. to pick up a newly added share before your next run):

```bash
janelia-paths-fetch
```

To point at a custom shares file (useful for testing or air-gapped setups), set:

```bash
export JANELIA_PATHLIB_SHARES_PATH=/path/to/shares.json
```

### Running offline (CI)

For environments that can't reach the network at all (e.g. CI on cloud runners), disable fetching entirely:

```bash
export JANELIA_PATHLIB_NO_FETCH=1
```

With this set, the network is never touched: the existing cache file is used as-is if present (however stale), or share data degrades to "no known shares" if not — same `to_janelia_uri`/`from_janelia_uri` behavior as a failed fetch, just without ever attempting the network call. This is also what this project's own test suite uses, pointed at a small fixture file via `JANELIA_PATHLIB_SHARES_PATH`, so tests never depend on (or accidentally overwrite) real network data.

The fetch also has a timeout (default 10s) so an unreachable network can't hang
for long; override it with:

```bash
export JANELIA_PATHLIB_FETCH_TIMEOUT=5
```

### Never mounting

On macOS, `from_janelia_uri` mounts a share that isn't mounted yet, which can
block on a credential prompt. To forbid that — so an unmounted share raises
instead — set:

```bash
export JANELIA_PATHLIB_NO_MOUNT=1
```

Already-mounted shares still resolve; this only stops new mounts. Use it in
cron jobs, CI, containers, or anywhere nobody is there to answer a prompt.

Whether a prompt can be answered is a property of the machine, not of the code
calling it, which is why this is an environment setting rather than a function
argument — the person running a script decides, not whoever wrote it.

### Mount timeout

When mounting *is* allowed, `from_janelia_uri` waits up to 30s for the mount to
appear. That wait covers a credential prompt, so it budgets for someone typing
a password — raise it for a slow authenticator:

```bash
export JANELIA_PATHLIB_MOUNT_TIMEOUT=90
```

Passing `timeout=` to `from_janelia_uri` overrides it per call.

## Development

This project uses [uv](https://docs.astral.sh/uv/) and
[just](https://just.systems). To set up a development environment:

```bash
git clone https://github.com/JaneliaSciComp/janelia_pathlib
cd janelia_pathlib
just install
```

See [CONTRIBUTING.md](https://github.com/JaneliaSciComp/janelia_pathlib/blob/main/CONTRIBUTING.md)
for more, or run `just` to list all available recipes.
