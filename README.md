# janelia-pathlib

Translate Janelia file share paths to and from an unambiguous `janelia://` URI, so code can refer to a share without depending on OS-specific mount guesses.

Janelia file shares are mounted at different paths on Linux, Mac, and Windows, and on macOS two different shares can even collide on the same guessed mount point (e.g. `/Volumes/shroff`). This package gives you two functions to move between a local path and a stable, host-qualified identity that's never ambiguous.

## Installation

```bash
uv add janelia-pathlib
```

Or, to track the latest unreleased commit:

```bash
uv add "janelia-pathlib @ git+https://github.com/JaneliaSciComp/janelia-pathlib"
```

Requires Python 3.12+. Zero runtime dependencies beyond `platformdirs`.

## Quick start

```python
from janelia_pathlib import to_janelia_uri, from_janelia_uri

# Canonicalize a local path to its unambiguous janelia:// identity
uri = to_janelia_uri("/labs/alpha/alphalab/sub/data")
# "janelia://fileserver.example.org/alphalab/sub/data"

# Resolve a janelia:// URI back to a real, usable local path
path = from_janelia_uri(uri)
# "/Volumes/alphalab/sub/data" (on Mac), "/labs/alpha/alphalab/sub/data" (on Linux), ...
```

## Why `janelia://`?

A share's local mount point differs by OS, and on macOS it isn't even guaranteed to be unique: two shares with the same share name on different servers (e.g. `nrs.hhmi.org/shroff` and `nearline.hhmi.org/shroff`) both nominally want `/Volumes/shroff` — macOS mounts the second at `/Volumes/shroff-1` instead. A bare path string like `/Volumes/shroff` can't tell you which share that actually is.

`janelia://host/share/subpath` encodes the SMB host and share name directly, which is always unique — so it's safe to store, log, or pass between machines, and always resolves back to the *correct* share regardless of what macOS happened to name the mountpoint.

## API

### `to_janelia_uri(path)`

Translate a local path in the **current OS's own format** to its canonical `janelia://host/share/subpath` form. A path in a different OS's format (e.g. a Windows UNC path passed in while running on Linux) is not recognized — only `janelia://` URIs are portable across OSes; a plain path is assumed to describe the machine it's running on.

```python
from janelia_pathlib import to_janelia_uri

# On Linux:
to_janelia_uri("/labs/alpha/alphalab/data")
# "janelia://fileserver.example.org/alphalab/data"

# On Mac, once the share is mounted:
to_janelia_uri("/Volumes/alphalab/data")
# "janelia://fileserver.example.org/alphalab/data"
```

**On macOS**, resolving a `/Volumes/...` path requires the share to be mounted already — `mac_path` alone can't be trusted as a guess (two shares can collide on the same mount name; see "Why `janelia://`?" above), so this checks live `mount` state rather than guessing. If the share isn't mounted, the path isn't recognized and passes through unchanged. Use `from_janelia_uri` first if you need to mount it.

**Linux** paths always resolve without any live check, since Linux mount points are fixed and never ambiguous.

Paths that don't match any known share are returned unchanged:

```python
to_janelia_uri("/tmp/local/scratch/file.txt")
# "/tmp/local/scratch/file.txt"
```

### `from_janelia_uri(path_str, *, timeout=30)`

Translate a `janelia://host/share/subpath` string to a real, usable local path.

```python
from janelia_pathlib import from_janelia_uri

from_janelia_uri("janelia://fileserver.example.org/alphalab/data")
# "/Volumes/alphalab/data" (on Mac)
# "/labs/alpha/alphalab/data" (on Linux)
```

On **macOS**, if the share isn't already mounted, this mounts it on demand (triggering the native SMB mount, which may prompt for credentials) and waits up to `timeout` seconds for the mount to appear, returning its *real* local mountpoint — not a guessed `/Volumes/<share>` path, which may not exist yet or may belong to a different, colliding share. Raises `RuntimeError` if the URI doesn't match a known share, or if mounting fails or times out.

On **Linux** and **Windows**, no mounting step is needed — shares are already available at a fixed path (Linux) or accessible directly via UNC path (Windows).

A string that isn't a `janelia://` URI is returned unchanged:

```python
from_janelia_uri("/tmp/local/scratch/file.txt")
# "/tmp/local/scratch/file.txt"
```

Both functions round-trip: `from_janelia_uri(to_janelia_uri(p))` resolves back to a valid local path for `p`'s share, and non-Janelia paths pass through both functions untouched.

## Share data

Share data is **not** bundled with the package — it's fetched at runtime from the [Fileglancer API](https://fileglancer.int.janelia.org/api/file-share-paths) into your user cache directory:

- **macOS**: `~/Library/Caches/janelia-pathlib/shares.json`
- **Linux**: `~/.cache/janelia-pathlib/shares.json`
- **Windows**: `%LOCALAPPDATA%\janelia-pathlib\Cache\shares.json`

The first time you import `janelia_pathlib` on a machine without a cached file, it auto-fetches from the API. This requires access to the Janelia network.

To refresh the cache (e.g. after new shares are added upstream):

```bash
janelia-paths-fetch
```

To point at a custom shares file (useful for testing or air-gapped setups), set:

```bash
export JANELIA_PATHLIB_SHARES_PATH=/path/to/shares.json
```

### Running offline (CI)

The auto-fetch needs the Janelia network, and by default a missing cache with no
network raises `RuntimeError`. For environments that can't reach the network
(e.g. CI on cloud runners), disable the fetch:

```bash
export JANELIA_PATHLIB_NO_FETCH=1
```

With this set and no cached share data, the registry loads no shares and paths
**pass through untranslated** instead of raising — so `to_janelia_uri`/`from_janelia_uri`
keep working on non-share paths exactly as if no shares were known.

The fetch also has a timeout (default 10s) so an unreachable network can't hang
the first import; override it with:

```bash
export JANELIA_PATHLIB_FETCH_TIMEOUT=5
```

## Releasing

The package version is derived automatically from git tags (via `hatch-vcs`) —
there's no version to bump in `pyproject.toml`. To cut a release, tag a commit
on `main` and push the tag:

```bash
git tag v0.2.0
git push origin v0.2.0
```

Pushing a `v*` tag runs the full test matrix in CI and, if it passes, builds
and publishes the package to PyPI via
[trusted publishing](https://docs.pypi.org/trusted-publishers/).
