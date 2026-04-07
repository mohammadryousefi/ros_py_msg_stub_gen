#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mohammad R. Yousefi
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Generate PyCharm-friendly .pyi stubs for ROS 2 message packages.

Goal:
- walk ROS-generated Python message packages already present on a ROS machine
- discover message packages automatically
- recurse through referenced message types across packages
- emit a stub tree that PyCharm can add as a Source Root / interpreter path

This script intentionally targets *editor support* (autocomplete, typo/member checks),
not ROS runtime execution.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import sys
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

KIND = "msg"

BASIC_TYPE_MAP = {
    "bool": "bool",
    "byte": "int",
    "char": "str",
    "float": "float",
    "double": "float",
    "float32": "float",
    "float64": "float",
    "int8": "int",
    "uint8": "int",
    "int16": "int",
    "uint16": "int",
    "int32": "int",
    "uint32": "int",
    "int64": "int",
    "uint64": "int",
    "octet": "int",
    "string": "str",
    "wstring": "str",
}


@dataclass
class PackageInfo:
    name: str
    root: Path
    package_dir: Path
    exports: dict[str, str] = field(default_factory=dict)  # symbol -> module basename, e.g. _header


@dataclass(frozen=True)
class RefType:
    package: str
    kind: str
    name: str


def log(msg: str, verbose: bool = False) -> None:
    if verbose:
        print(msg, file=sys.stderr)


def discover_default_roots() -> list[Path]:
    """
    Return ordered, existing directories from the *current Python import environment*.

    This intentionally mirrors the currently sourced shell / interpreter resolution
    as closely as possible. It does not walk AMENT/COLCON roots or mutate sys.path.
    """
    roots: list[Path] = []
    seen: set[Path] = set()

    for entry in sys.path:
        if not entry:
            continue
        try:
            p = Path(entry).resolve()
        except OSError:
            continue
        if p.is_dir() and p not in seen:
            seen.add(p)
            roots.append(p)

    return roots


def parse_exports(init_file: Path, package: str) -> dict[str, str]:
    """
    Parse lines like:
        from std_msgs.msg._header import Header
    and return {"Header": "_header"}.
    """
    exports: dict[str, str] = {}
    if not init_file.is_file():
        return exports

    text = init_file.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(init_file))
    expected_prefix = f"{package}.{KIND}."

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.startswith(expected_prefix):
                module_basename = node.module.rsplit(".", 1)[-1]
                for alias in node.names:
                    if alias.name != "*":
                        exports[alias.asname or alias.name] = module_basename
    return exports


def discover_packages(roots: Iterable[Path], verbose: bool = False) -> dict[str, PackageInfo]:
    """
    Discover candidate message packages by scanning roots in order, but always
    resolve the winning package via Python import semantics from the *current*
    environment. The scan finds names; import resolution decides which duplicate
    package actually applies.
    """
    found: dict[str, PackageInfo] = {}

    for root in roots:
        if not root.is_dir():
            continue

        try:
            children = list(root.iterdir())
        except OSError:
            continue

        for child in children:
            if not child.is_dir():
                continue
            if not (child / "__init__.py").exists():
                continue
            kind_dir = child / KIND
            init_file = kind_dir / "__init__.py"
            if not init_file.exists():
                continue

            package = child.name
            if package in found:
                continue

            pkg_info = discover_package_via_import(package, verbose=verbose)
            if pkg_info is None:
                log(
                    f"skipping package candidate {package} under {root}: "
                    f"not importable in current environment",
                    verbose,
                )
                continue

            found[package] = pkg_info
            log(
                f"discovered package {package} under {root}; "
                f"resolved to {pkg_info.package_dir}",
                verbose,
            )

    return found


def discover_package_via_import(package: str, verbose: bool = False) -> PackageInfo | None:
    try:
        importlib.import_module(f"{package}.{KIND}")
    except Exception:
        return None

    package_mod = importlib.import_module(package)
    package_file = getattr(package_mod, "__file__", None)
    if not package_file:
        return None

    package_dir = Path(package_file).resolve().parent
    root = package_dir.parent
    init_file = package_dir / KIND / "__init__.py"
    if not init_file.exists():
        return None

    exports = parse_exports(init_file, package)
    if not exports:
        return None

    log(f"discovered package {package} via import", verbose)
    return PackageInfo(name=package, root=root, package_dir=package_dir, exports=exports)


def import_msg_module(package: str, module_basename: str):
    return importlib.import_module(f"{package}.{KIND}.{module_basename}")


def type_class_name(obj: Any) -> str:
    return obj.__class__.__name__


def namespaced_info(slot_type: Any) -> tuple[str, str, str] | None:
    cname = type_class_name(slot_type)
    if cname != "NamespacedType":
        return None
    namespaces = list(getattr(slot_type, "namespaces", []) or [])
    name = getattr(slot_type, "name", None)
    if not name or not namespaces:
        return None
    package = namespaces[0]
    kind = namespaces[1] if len(namespaces) > 1 else KIND
    return package, kind, name


def nested_value_type(slot_type: Any) -> Any | None:
    for attr in ("value_type", "content_type"):
        if hasattr(slot_type, attr):
            return getattr(slot_type, attr)
    return None


def render_slot_type(slot_type: Any) -> tuple[str, set[RefType]]:
    deps: set[RefType] = set()
    cname = type_class_name(slot_type)

    info = namespaced_info(slot_type)
    if info is not None:
        dep_pkg, dep_kind, dep_name = info
        deps.add(RefType(dep_pkg, dep_kind, dep_name))
        return dep_name, deps

    typename = getattr(slot_type, "typename", None)
    if typename is not None:
        return BASIC_TYPE_MAP.get(str(typename), "typing.Any"), deps

    if "String" in cname or "WString" in cname:
        return "str", deps

    inner = nested_value_type(slot_type)
    if inner is not None:
        inner_type, inner_deps = render_slot_type(inner)
        deps |= inner_deps
        if "Sequence" in cname or cname == "Array":
            return f"list[{inner_type}]", deps
        return inner_type, deps

    return "typing.Any", deps


def get_msg_fields(cls: type) -> list[tuple[str, str, set[RefType]]]:
    fields_map = {}
    if hasattr(cls, "get_fields_and_field_types"):
        try:
            fields_map = cls.get_fields_and_field_types()
        except Exception:
            fields_map = getattr(cls, "_fields_and_field_types", {}) or {}
    else:
        fields_map = getattr(cls, "_fields_and_field_types", {}) or {}

    field_names = list(fields_map.keys())
    slot_types = list(getattr(cls, "SLOT_TYPES", ()))

    rendered: list[tuple[str, str, set[RefType]]] = []
    for idx, field_name in enumerate(field_names):
        if idx < len(slot_types):
            type_str, deps = render_slot_type(slot_types[idx])
        else:
            type_str, deps = "typing.Any", set()
        rendered.append((field_name, type_str, deps))
    return rendered


def make_import_lines(
    package: str,
    current_module: str,
    refs: set[RefType],
    packages: dict[str, PackageInfo],
) -> list[str]:
    lines: set[str] = set()
    for ref in sorted(refs, key=lambda r: (r.package, r.kind, r.name)):
        if ref.kind != KIND:
            continue
        if ref.package == package:
            pkg_info = packages.get(package)
            if not pkg_info:
                continue
            ref_module = pkg_info.exports.get(ref.name)
            if ref_module and ref_module != current_module:
                lines.add(f"from .{ref_module} import {ref.name}")
        else:
            lines.add(f"from {ref.package}.{ref.kind} import {ref.name}")
    return sorted(lines)


def render_msg_stub(
    package: str,
    symbol: str,
    module_basename: str,
    packages: dict[str, PackageInfo],
) -> tuple[str, set[str]]:
    mod = import_msg_module(package, module_basename)
    cls = getattr(mod, symbol)
    fields = get_msg_fields(cls)

    refs: set[RefType] = set()
    for _, _, field_refs in fields:
        refs |= field_refs

    import_lines = make_import_lines(package, module_basename, refs, packages)

    needs_typing = any(type_str.startswith("typing.") for _, type_str, _ in fields)
    lines = [
        "from __future__ import annotations",
        "",
    ]
    if needs_typing:
        lines.append("import typing")
        lines.append("")
    if import_lines:
        lines.extend(import_lines)
        lines.append("")

    lines.append(f"class {symbol}:")
    if not fields:
        lines.append("    def __init__(self) -> None: ...")
    else:
        for field_name, type_str, _ in fields:
            lines.append(f"    {field_name}: {type_str}")
        lines.append("")
        params = ", ".join(f"{field_name}: {type_str} = ..." for field_name, type_str, _ in fields)
        lines.append(f"    def __init__(self, *, {params}) -> None: ...")
    lines.append("")

    dep_packages = {ref.package for ref in refs if ref.kind == KIND and ref.package != package}
    return "\n".join(lines), dep_packages


def ensure_package_output_dirs(output: Path, package: str) -> tuple[Path, Path]:
    pkg_dir = output / package
    msg_dir = pkg_dir / KIND
    msg_dir.mkdir(parents=True, exist_ok=True)
    return pkg_dir, msg_dir


def write_package_init_files(output: Path, pkg_info: PackageInfo) -> None:
    pkg_dir, msg_dir = ensure_package_output_dirs(output, pkg_info.name)

    top_init = pkg_dir / "__init__.pyi"
    if not top_init.exists():
        top_init.write_text("from . import msg\n", encoding="utf-8")

    exports_lines = [
        "from __future__ import annotations",
        "",
    ]
    for symbol, module_basename in sorted(pkg_info.exports.items()):
        exports_lines.append(f"from .{module_basename} import {symbol}")
    exports_lines.append("")
    exports_lines.append("__all__ = [")
    for symbol in sorted(pkg_info.exports):
        exports_lines.append(f"    '{symbol}',")
    exports_lines.append("]")
    exports_lines.append("")

    (msg_dir / "__init__.pyi").write_text("\n".join(exports_lines), encoding="utf-8")
    (pkg_dir / "py.typed").write_text("", encoding="utf-8")


def build_stubs(
    output: Path,
    requested_packages: list[str] | None,
    roots: list[Path],
    verbose: bool = False,
) -> tuple[dict[str, PackageInfo], list[str], list[str]]:
    packages = discover_packages(roots, verbose=verbose)
    if requested_packages:
        start = list(dict.fromkeys(requested_packages))
    else:
        start = list(packages.keys())

    processed_modules: set[tuple[str, str]] = set()
    processed_packages: set[str] = set()
    generated: list[str] = []
    failed: list[str] = []

    queue = deque(start)

    while queue:
        package = queue.popleft()
        if package in processed_packages:
            continue

        pkg_info = packages.get(package)
        if pkg_info is None:
            pkg_info = discover_package_via_import(package, verbose=verbose)
            if pkg_info is None:
                failed.append(package)
                log(f"could not discover package {package}", verbose)
                continue
            packages[package] = pkg_info

        write_package_init_files(output, pkg_info)

        for symbol, module_basename in sorted(pkg_info.exports.items()):
            mod_key = (package, module_basename)
            if mod_key in processed_modules:
                continue
            try:
                stub_text, dep_packages = render_msg_stub(
                    package=package,
                    symbol=symbol,
                    module_basename=module_basename,
                    packages=packages,
                )
                _, msg_dir = ensure_package_output_dirs(output, package)
                out_file = msg_dir / f"{module_basename}.pyi"
                out_file.write_text(stub_text, encoding="utf-8")
                generated.append(str(out_file))
                processed_modules.add(mod_key)

                for dep_pkg in sorted(dep_packages):
                    if dep_pkg not in processed_packages:
                        queue.append(dep_pkg)

            except Exception:
                failed.append(f"{package}.{KIND}.{module_basename}")
                log(f"failed while generating stub for {package}.{KIND}.{module_basename}", verbose)
                if verbose:
                    traceback.print_exc()

        processed_packages.add(package)

    return packages, generated, failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate .pyi stubs for ROS 2 generated Python message packages."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ros_stubs"),
        help="Directory where the stub tree will be written.",
    )
    parser.add_argument(
        "--roots",
        nargs="*",
        type=Path,
        help="Optional roots to scan for package names. Import resolution still follows the current Python environment.",
    )
    parser.add_argument(
        "--packages",
        nargs="*",
        help="Optional package names to seed generation. Dependencies are explored recursively.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress and tracebacks.",
    )

    args = parser.parse_args()
    roots = args.roots or discover_default_roots()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    packages, generated, failed = build_stubs(
        output=output,
        requested_packages=args.packages,
        roots=roots,
        verbose=args.verbose,
    )

    summary = [
        f"roots_scanned={len(roots)}",
        f"packages_discovered={len(packages)}",
        f"stub_files_written={len(generated)}",
        f"failed_items={len(failed)}",
        f"output={output}",
    ]
    print(" ".join(summary))

    if failed:
        print("Failures:", file=sys.stderr)
        for item in failed:
            print(f"  - {item}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
