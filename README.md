# janelia-pathlib

A `pathlib.Path` subclass that auto-translates Janelia file share paths across operating systems.

Janelia file shares are mounted at different paths on Linux, Mac, and Windows. This package lets you write a path from any OS and have it automatically resolve to the correct mount point on whatever machine your code is running on.

## Installation

```bash
uv add janelia-pathlib
```

Requires Python 3.12+. Zero external dependencies.

## Quick start

```python
from janelia_pathlib import JaneliaPath

# A Linux path used on Mac automatically becomes /Volumes/alphalab/sub/data
p = JaneliaPath("/labs/alpha/alphalab/sub/data")

# It's a real pathlib.Path — use it anywhere you'd use Path
for f in p.glob("*.zarr"):
    print(f)
```

## Usage examples

### Auto-translation in constructors

`JaneliaPath` detects which OS the input path belongs to and translates it to the current OS:

```python
from janelia_pathlib import JaneliaPath

# On Mac:
p = JaneliaPath("/labs/alpha/alphalab/experiment/results.csv")
str(p)  # "/Volumes/alphalab/experiment/results.csv"

# On Linux:
p = JaneliaPath("/Volumes/alphalab/experiment/results.csv")
str(p)  # "/labs/alpha/alphalab/experiment/results.csv"

# Windows UNC paths work too:
p = JaneliaPath(r"\\fileserver.example.org\alphalab\experiment\results.csv")
```

Paths that don't match any known share pass through unchanged:

```python
p = JaneliaPath("/tmp/local/scratch/file.txt")
str(p)  # "/tmp/local/scratch/file.txt"
```

### Constructing from group name

If you know the lab group and storage type, use `get_path()`:

```python
from janelia_pathlib import get_path, Storage

# Get the primary share for the Bravo lab (on Mac)
p = get_path("bravo")
str(p)  # "/Volumes/bravolab"

# Specify a storage type
p = get_path("bravo", Storage.HOME)
str(p)  # "/Volumes/bravo$"

# Chain with subdirectories
p = get_path("bravo") / "sub" / "data"
str(p)  # "/Volumes/bravolab/sub/data"
```

Also available as a classmethod: `JaneliaPath.from_share("bravo", Storage.HOME)`.

### Path joining with `/`

The `/` operator works as expected and preserves the `JaneliaPath` type:

```python
p = JaneliaPath("/labs/bravo/bravolab") / "sub" / "data"
type(p)  # <class 'janelia_pathlib.JaneliaPath'>
```

Incremental construction also works — translation is re-evaluated on each join, so a partial prefix that doesn't match any share will translate once the full share path is formed:

```python
# "/labs/bravo" alone doesn't match any share, so it passes through
p = JaneliaPath("/labs/bravo")
str(p)  # "/labs/bravo"

# Joining completes the share path, triggering translation
p = p / "bravolab"
str(p)  # "/Volumes/bravolab"  (on Mac)
```

### Auto-mounting shares (macOS)

On macOS, if a share isn't mounted yet, call `.mount()` to mount it automatically via SMB:

```python
p = JaneliaPath("/labs/alpha/alphalab/sub/data")

# Mount the share if not already mounted (blocks until ready)
p.mount()

# Now you can use the path normally
for f in p.glob("*.zarr"):
    print(f)
```

`.mount()` returns `True` if the share is available after the operation, `False` if it timed out. It's a no-op if the share is already mounted. Only works on macOS — raises `RuntimeError` on other platforms.

### Explicit cross-OS translation with `to_os()`

Translate a path to a specific OS without depending on what machine you're on:

```python
p = JaneliaPath("/Volumes/bravolab/sub/data")

p.to_os("linux")    # JaneliaPath('/labs/bravo/bravolab/sub/data')
p.to_os("windows")  # JaneliaPath('\\\\fileserver.example.org\\bravolab\\sub\\data')
p.to_os("mac")      # JaneliaPath('/Volumes/bravolab/sub/data')
```

### Inspecting share metadata

The `.share` property returns a `Share` dataclass for the matched file share:

```python
p = JaneliaPath("/labs/charlie/charlie/data")

p.share.name      # "Charlie"
p.share.group     # "charlie"
p.share.storage   # "primary"
p.share.linux_path    # "/labs/charlie/charlie"
p.share.mac_path      # "/Volumes/charlie"
p.share.windows_path  # "\\\\fileserver.example.org\\charlie"
```

Returns `None` for paths that don't match any share:

```python
JaneliaPath("/tmp/file.txt").share  # None
```

### Functional API

If you don't need the `Path` subclass, use `translate()`, `resolve()`, and `get_share()` directly:

```python
from janelia_pathlib import translate, resolve, get_share

# Translate to a specific OS
translate("/labs/bravo/bravolab/sub/data", "mac")
# "/Volumes/bravolab/sub/data"

translate("/labs/bravo/bravolab/sub/data", "windows")
# "\\\\fileserver.example.org\\bravolab\\sub\\data"

# Returns None for unrecognized paths
translate("/tmp/local/file.txt", "mac")  # None

# resolve() returns the matched Share and the relative subpath
share, subpath = resolve("/labs/bravo/bravolab/sub/data")
share.name   # "Bravo"
subpath      # "sub/data"

# get_share() looks up a share by group and storage type
share = get_share("charlie", "primary")
share.linux_path  # "/labs/charlie/charlie"
```

## API reference

### `JaneliaPath(*args)`

A `pathlib.Path` subclass. The constructor accepts the same arguments as `Path()` and auto-translates any recognized Janelia file share path to the current OS.

**Class methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `from_share(group, storage="primary")` | `JaneliaPath` | Construct from group name and storage type |

**Methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `to_os(target_os)` | `JaneliaPath` | Translate to `"linux"`, `"mac"`, or `"windows"` |
| `mount(*, timeout=30)` | `bool` | Mount the share via SMB (macOS only) |

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `share` | `Share \| None` | The matched share entry, or `None` |

All standard `pathlib.Path` methods and properties (`exists()`, `read_text()`, `glob()`, `parent`, `name`, `stem`, etc.) work as expected.

### `get_path(group, storage="primary")`

Get a `JaneliaPath` for a share by group name and storage type. Returns the path resolved to the current OS. Raises `KeyError` if no match is found.

### `translate(path_str, target_os=None)`

Translate a path string to the target OS format. Returns `None` if the path doesn't match any known share. Defaults to the current OS if `target_os` is omitted.

### `resolve(path_str)`

Match a path string against known shares. Returns `(Share, subpath)` where `subpath` is the relative portion after the share mount prefix, or `None` if no share matches.

### `get_share(group, storage="primary")`

Look up a `Share` by group name and storage type. Raises `KeyError` if no match is found.

### `current_os()`

Returns the current OS as `"linux"`, `"mac"`, or `"windows"`.

### `Share`

A frozen dataclass with fields: `name`, `group`, `storage`, `linux_path`, `mac_path`, `windows_path`.

### `Storage`

A `StrEnum` of storage types. Can be used as plain strings or enum values:

| Value | String | Description |
|-------|--------|-------------|
| `Storage.PRIMARY` | `"primary"` | Main lab file share |
| `Storage.HOME` | `"home"` | User home directories |
| `Storage.SCRATCH` | `"scratch"` | Scratch storage |
| `Storage.ARCHIVE` | `"archive"` | Archive / nearline storage |
| `Storage.NEARLINE` | `"nearline"` | Nearline storage (legacy, same as archive) |

Since `Storage` is a `StrEnum`, you can use either `Storage.HOME` or the string `"home"` interchangeably.

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
