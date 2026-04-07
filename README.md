# ROS2 Message Stub Generator
## Author
Mohammad R. Yousefi

## License


## Overview

`ros2_stubgen.py` generates Python stub files (`.pyi`) for ROS 2 message packages that are importable in the **currently active Python / ROS environment**.

The generated stubs are intended for **IDE support**, not runtime execution. The primary use cases are:

- autocomplete
- member and constructor keyword typo detection
- field navigation
- nested message type discovery across packages
- static inspection in IDEs such as PyCharm

The generator follows the environment that is already active when the script runs. In practice, this means it should be executed from a shell where the desired ROS 2 `setup.bash` files have already been sourced.

---

## Design Goals

The script is built around these rules:

1. **Current environment is the source of truth**  
   It does not try to discover ROS installs on its own when default behavior is used. If `--roots` is omitted, package discovery follows the current `sys.path`.

2. **Overlay behavior should match current resolution**  
   If a package is overridden by an overlay, the version currently importable in Python is the version used to generate stubs.

3. **Generate for the active ROS message ecosystem**  
   If no package list is provided, the script scans the active import roots for message packages and generates stubs for all resolvable `msg` packages it finds.

4. **Recursively follow nested message dependencies**  
   If one message references another message in the same or another package, the dependency package is also explored and stubbed.

5. **IDE support only**  
   The output is not meant to make ROS run on a non-ROS machine. It is meant to make editors understand the message namespaces, classes, and fields.

---

## What It Generates

For each resolved message package, the script writes a stub package tree that mirrors the ROS package layout.

Example output:

```text
ros_stubs/
├── std_msgs/
│   ├── __init__.pyi
│   ├── py.typed
│   └── msg/
│       ├── __init__.pyi
│       ├── _bool.pyi
│       ├── _float64_multi_array.pyi
│       └── ...
├── geometry_msgs/
│   ├── __init__.pyi
│   ├── py.typed
│   └── msg/
│       ├── __init__.pyi
│       ├── _pose.pyi
│       └── ...
└── my_msgs/
    ├── __init__.pyi
    ├── py.typed
    └── msg/
        ├── __init__.pyi
        ├── _message_type.pyi
        └── ...
```

This lets an IDE resolve imports such as:

```python
from std_msgs.msg import Float64MultiArray
from geometry_msgs.msg import Pose
from my_msgs.msg import MessageType
```

---

## Resolution Model

### Default behavior

If `--roots` is **not** provided, the script scans the current Python import roots from `sys.path`.

That means:

- whatever has been sourced into the current shell applies
- overlay precedence comes from the active environment
- the script does not reorder or override that precedence
- the importable version of a package is the version that gets stubbed

### Explicit roots

If `--roots` **is** provided, the script scans those roots for candidate packages. However, package resolution is still confirmed through Python import semantics.

So the model is:

- scanning finds candidate package names
- Python import resolution decides what package actually wins

This is intentional. The script is designed so that the package it stubs is the package Python would import in the active environment.

---

## Supported Scope

### Currently supported

- ROS 2 **message** packages (`msg`)
- nested message references across packages
- primitive field types
- sequence / array-like fields mapped into Python list-style annotations
- generated Python message packages already available in the active environment

### Not currently supported

- `srv`
- `action`
- ROS runtime behavior on a machine without ROS
- C type support generation
- validation logic for semantic constraints beyond field shape/type exposure in stubs

---

## Type Mapping Behavior

The script inspects generated ROS 2 Python message classes and maps ROS field types into Python type annotations for stub output.

Typical mappings include:

- `bool` → `bool`
- integer variants → `int`
- `float32`, `float64`, `double` → `float`
- `string`, `wstring` → `str`
- sequences / arrays → `list[...]`
- nested message types → referenced message class names

Examples:

```python
class Bool:
    data: bool
```

```python
class Float64MultiArray:
    layout: MultiArrayLayout
    data: list[float]
```

Important note for multi-dimensional array message types such as `Float64MultiArray`:

- the `data` field becomes a flat `list[float]`
- shape metadata lives in the `layout` field
- the stub expresses the Python-visible field structure, not semantic shape validation

---

## How It Works

The high-level flow is:

1. determine the roots to scan
2. find packages that look like Python ROS message packages
3. resolve each package through Python import semantics
4. parse the package's `msg/__init__.py` exports
5. import each generated message module
6. inspect the generated class metadata
7. emit a `.pyi` file for each message module
8. follow referenced message-package dependencies recursively

### Package discovery

A package is considered a candidate when the scanner sees:

- a Python package directory with `__init__.py`
- a `msg/` subdirectory
- a `msg/__init__.py` file

### Export discovery

The script parses `msg/__init__.py` for lines like:

```python
from std_msgs.msg._header import Header
```

That gives it the public symbol-to-module mapping, for example:

- `Header` → `_header`
- `Float64MultiArray` → `_float64_multi_array`

### Field inspection

For each message class, the script reads:

- `get_fields_and_field_types()` when available
- `_fields_and_field_types` as fallback
- `SLOT_TYPES` for structural type information

That is how it learns the field names and field types to place in the stub.

### Dependency expansion

If a field type refers to another ROS message, the script records that dependency and queues the referenced package for generation if it has not already been processed.

---

## Command-Line Interface

### Basic syntax

```bash
python3 ros2_stubgen.py [OPTIONS]
```

### Options

#### `--output`
Output directory for generated stubs.

Default:

```text
ros_stubs
```

Example:

```bash
python3 ros2_stubgen.py --output ./ide_stubs
```

#### `--packages`
Optional seed list of packages to generate.

If omitted, all discoverable message packages in the current environment are considered.

Example:

```bash
python3 ros2_stubgen.py --packages my_msgs std_msgs geometry_msgs
```

When `--packages` is used, referenced dependency packages are still explored recursively.

#### `--roots`
Optional explicit roots to scan for candidate packages.

Use this only when you want to constrain or redirect discovery explicitly.

Example:

```bash
python3 ros2_stubgen.py --roots /opt/ros/humble/lib/python3.10/site-packages
```

#### `--verbose`
Prints progress messages and full tracebacks for failures.

Example:

```bash
python3 ros2_stubgen.py --verbose
```

---

## Recommended Usage

### Generate stubs for everything visible in the active environment

```bash
source /opt/ros/<distro>/setup.bash
source /path/to/overlay/install/setup.bash
python3 ros2_stubgen.py --output ./ros_stubs
```

This is the most typical mode.

### Generate stubs for a specific package set

```bash
source /opt/ros/<distro>/setup.bash
source /path/to/overlay/install/setup.bash
python3 ros2_stubgen.py     --packages my_msgs std_msgs geometry_msgs     --output ./ros_stubs
```

This is useful when you want to reduce output size while still allowing dependency expansion.

### Constrain scanning to a known root

```bash
python3 ros2_stubgen.py     --roots /opt/ros/humble/lib/python3.10/site-packages     --packages std_msgs     --output ./ros_stubs
```

---

## PyCharm Integration

Once the stubs are generated, copy the output tree to the development environment where you want IDE support.

Then add the output directory to PyCharm as either:

- a **Source Root**
- or part of the interpreter's visible search path

After that, imports like these should resolve for editor purposes:

```python
from my_msgs.msg import MessageType
from std_msgs.msg import Float64MultiArray
```

Expected IDE behavior includes:

- completion for message classes
- completion for member fields
- constructor keyword suggestions
- typo detection for invalid attribute names
- navigation into nested message field types

---

## Example Stub Output

A generated message stub typically looks like this:

```python
from __future__ import annotations

from ._multi_array_layout import MultiArrayLayout

class Float64MultiArray:
    layout: MultiArrayLayout
    data: list[float]

    def __init__(self, *, layout: MultiArrayLayout = ..., data: list[float] = ...) -> None: ...
```

That is enough for an IDE to understand the public shape of the message without requiring ROS runtime support.

---

## Failure Handling

At the end of the run, the script prints a summary like:

```text
roots_scanned=... packages_discovered=... stub_files_written=... failed_items=... output=...
```

If failures occur, they are listed on stderr.

Common reasons for failure include:

- a package is visible during scanning but not importable in the current environment
- a generated module raises an error when imported
- a message type includes a structure the current type renderer does not yet handle cleanly

Using `--verbose` will show more detail and full tracebacks.

---

## Limitations and Boundaries

### 1. Message packages only
The script currently targets `msg` packages only. It does not generate stubs for:

- services
- actions
- other ROS interfaces

### 2. IDE model, not runtime model
The output is meant for static tooling. It does not include:

- runtime conversion support
- ROS middleware bindings
- C type support
- serialization behavior

### 3. Field exposure, not semantic validation
The generated stubs expose field names and field types. They do not attempt to enforce higher-level semantic constraints such as:

- array length matching layout metadata
- stride consistency
- application-specific invariants

### 4. Depends on importable generated Python packages
The script expects ROS-generated Python message packages to already exist and be importable in the active environment.

---

## Troubleshooting

### The wrong package version appears to be used
Make sure the intended ROS 2 setup files have been sourced in the current shell before running the script.

Check what Python resolves:

```bash
python3 - <<'PY'
import my_msgs
print(my_msgs.__file__)
PY
```

The script follows the same active import environment.

### A package is skipped
A package candidate can be skipped if it is visible during scanning but not actually importable as a ROS Python package in the current environment.

Run with:

```bash
python3 ros2_stubgen.py --verbose
```

### IDE still does not resolve the generated package
Make sure the generated output directory itself is added to PyCharm, not one of the nested package directories.

Correct:

- add `ros_stubs/`

Incorrect:

- add `ros_stubs/std_msgs/`

### Nested messages do not resolve
Generate stubs either:

- without `--packages`, so the whole visible ecosystem is considered
- or with the seed package plus any additional packages you know you use frequently

The script does recurse through discovered message dependencies, but only for dependency structures it can observe from the imported generated classes.

---

## Intended Workflow

1. source the desired ROS 2 environment
2. run `ros2_stubgen.py`
3. copy the generated stub tree to the non-ROS development environment
4. add the stub root to PyCharm
5. write code against ROS message namespaces with IDE support

---

## Summary

`ros2_stubgen.py` is an environment-driven ROS 2 message stub generator for IDE use.

Its key properties are:

- uses the current sourced Python / ROS resolution as truth
- respects active overlay behavior
- generates `.pyi` stubs for message packages
- recursively follows nested message dependencies
- supports autocomplete and typo detection in IDEs
- does not attempt to provide ROS runtime support

