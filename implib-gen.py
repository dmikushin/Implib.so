#!/usr/bin/env python3

# Copyright 2017-2025 Yury Gribov
#
# The MIT License (MIT)
#
# Use of this source code is governed by MIT license that can be
# found in the LICENSE.txt file.

"""
Generates static import library for POSIX shared library
"""

import sys
import os
import os.path
import re
import subprocess
import argparse
import string
import configparser

me = os.path.basename(__file__)
root = os.path.dirname(__file__)


def warn(msg):
    """Emits a nicely-decorated warning."""
    sys.stderr.write(f"{me}: warning: {msg}\n")


def error(msg):
    """Emits a nicely-decorated error and exits."""
    sys.stderr.write(f"{me}: error: {msg}\n")
    sys.exit(1)


def run(args, stdin=""):
    """Runs external program and aborts on error."""
    env = os.environ.copy()
    # Force English language
    env["LC_ALL"] = "c"
    try:
        del env["LANG"]
    except KeyError:
        pass
    with subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    ) as p:
        out, err = p.communicate(input=stdin.encode("utf-8"))
    out = out.decode("utf-8")
    err = err.decode("utf-8")
    if p.returncode != 0 or err:
        error(f"{args[0]} failed with retcode {p.returncode}:\n{err}")
    return out, err


def is_binary_file(filename):
    """Check if file is an ELF or Mach-O binary."""
    # First try readelf for ELF files
    cmd = ["readelf", "-d", filename]
    with subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as p:
        p.communicate()
    if p.returncode == 0:
        return True

    # If readelf fails, try file command for Mach-O files (macOS)
    try:
        cmd = ["file", filename]
        with subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as p:
            out, _ = p.communicate()
            output = out.decode("utf-8", errors="ignore")
            # Check for Mach-O binary signatures
            return any(sig in output for sig in ["Mach-O", "shared library"])
    except (OSError, UnicodeDecodeError):
        pass

    return False


def make_toc(words, renames=None):
    "Make an mapping of words to their indices in list"
    renames = renames or {}
    toc = {}
    for i, n in enumerate(words):
        name = renames.get(n, n)
        toc[i] = name
    return toc


def parse_row(words, toc, hex_keys):
    "Make a mapping from column names to values"
    vals = {k: (words[i] if i < len(words) else "") for i, k in toc.items()}
    for k in hex_keys:
        if vals[k]:
            vals[k] = int(vals[k], 16)
    return vals


def collect_syms(f):
    """Collect ELF dynamic symtab and determine visibility using nm."""

    # Use nm to determine visibility
    nm_out, _ = run(["nm", "-g", f])

    # Parse nm output to get visibility
    visibility = {}
    for line in nm_out.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            symbol_type = parts[1]
            symbol_name = parts[2]
            # Global symbols have uppercase types, local/weak have lowercase
            visibility[symbol_name] = "DEFAULT" if symbol_type.isupper() else "HIDDEN"

    # Check if this is a Mach-O file (macOS)
    try:
        # Try to detect Mach-O files
        file_out, _ = run(["file", f])
        is_macho = "Mach-O" in file_out
    except (OSError, subprocess.SubprocessError):
        is_macho = False

    if is_macho:
        # Use nm for Mach-O files to extract symbols
        nm_detailed_out, _ = run(["nm", "-D", f])
        readelf_out = nm_detailed_out
    else:
        # Use readelf for ELF files
        readelf_out, _ = run(["readelf", "-sW", f])

    syms = []
    syms_set = set()

    if is_macho:
        # Parse nm output for Mach-O files (simplified)
        for line in readelf_out.splitlines():
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            if len(parts) >= 3:
                address = parts[0]
                symbol_type = parts[1]
                name = parts[2]

                if name in syms_set:
                    continue
                syms_set.add(name)

                # Create minimal symbol info for Mach-O
                sym = {
                    "Name": name,
                    "Value": int(address, 16) if address != "U" else 0,
                    "Size": 0,  # nm doesn't provide size info
                    "Type": "FUNC" if symbol_type.upper() == "T" else "OBJECT",
                    "Bind": "GLOBAL" if symbol_type.isupper() else "LOCAL",
                    "Ndx": "1" if symbol_type.upper() != "U" else "UND",
                    "Default": True,
                    "Version": None,
                    "Visibility": visibility.get(name, "DEFAULT"),
                }
                syms.append(sym)
    else:
        # Parse readelf output for ELF files
        toc = None

        for line in readelf_out.splitlines():
            line = line.strip()

            # Strip out strange markers in powerpc64le ELFs
            line = re.sub(r"\[<localentry>: [0-9]+\]", "", line)

            if not line:
                # Next symtab
                toc = None
                continue

            words = re.split(r" +", line)

            if line.startswith("Num"):  # Header?
                if toc is not None:
                    error("multiple headers in output of readelf")
                # Colons are different across readelf versions so get rid of them.
                toc = make_toc(map(lambda n: n.replace(":", ""), words))
            elif toc is not None:
                sym = parse_row(words, toc, ["Value"])
                name = sym["Name"]
                if not name:
                    continue
                if name in syms_set:
                    continue
                syms_set.add(name)
                sym["Size"] = int(
                    sym["Size"], 0
                )  # Readelf is inconsistent on Size format
                if "@" in name:
                    sym["Default"] = "@@" in name
                    name, ver = re.split(r"@+", name)
                    sym["Name"] = name
                    sym["Version"] = ver
                else:
                    sym["Default"] = True
                    sym["Version"] = None

                # Add visibility information
                sym["Visibility"] = visibility.get(name, "DEFAULT")
                syms.append(sym)

    if not is_macho and toc is None:
        error(f"failed to analyze symbols in {f}")

    return syms


def collect_def_exports(filename):
    """Reads exported symbols from .def file."""

    syms = []

    try:
        with open(filename, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (UnicodeDecodeError, IOError):
        # Not a text file, return empty list
        return syms
    lines.reverse()

    while lines:
        line = lines.pop().strip()

        if line != "EXPORTS":
            continue

        while lines:
            line = lines.pop()

            if re.match(r"^\s*;", line):  # Comment
                continue

            # TODO: support renames
            m = re.match(r"^\s+([A-Za-z0-9_]+)\s*$", line)
            if m is None:
                lines.append(line)
                break

            sym = {
                "Name": m[1],
                "Bind": "GLOBAL",
                "Type": "FUNC",
                "Ndx": "0",
                "Default": True,
                "Version": None,
                "Size": 0,
                "Visibility": "DEFAULT",
            }
            syms.append(sym)

    if not syms:
        warn(f"failed to locate symbols in {filename}")

    return syms


def collect_relocs(f):
    """Collect ELF dynamic relocs."""

    # Check if this is a Mach-O file (macOS)
    try:
        file_out, _ = run(["file", f])
        if "Mach-O" in file_out:
            # Return empty list for Mach-O files - relocations not supported yet
            return []
    except (OSError, subprocess.SubprocessError):
        pass

    out, _ = run(["readelf", "-rW", f])

    toc = None
    rels = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            toc = None
            continue
        if line == "There are no relocations in this file.":
            return []
        if re.match(r"^\s*Type[0-9]:", line):  # Spurious lines for MIPS
            continue
        if re.match(r"^\s*Offset", line):  # Header?
            if toc is not None:
                error("multiple headers in output of readelf")
            words = re.split(r"\s\s+", line)  # "Symbol's Name + Addend"
            toc = make_toc(words)
        elif re.match(r"^\s*r_offset", line):  # FreeBSD header?
            if toc is not None:
                error("multiple headers in output of readelf")
            words = re.split(r"\s\s+", line)  # "st_name + r_addend"
            toc = make_toc(words)
            rename = {
                "r_offset": "Offset",
                "r_info": "Info",
                "r_type": "Type",
                "st_value": "Symbol's Value",
                "st_name + r_addend": "Symbol's Name + Addend",
            }
            toc = {idx: rename[name] for idx, name in toc.items()}
        elif toc is not None:
            line = re.sub(r" \+ ", "+", line)
            words = re.split(r"\s+", line)
            rel = parse_row(words, toc, ["Offset", "Info"])
            rels.append(rel)
            # Split symbolic representation
            sym_name = "Symbol's Name + Addend"
            if sym_name not in rel and "Symbol's Name" in rel:
                # Adapt to different versions of readelf
                rel[sym_name] = rel["Symbol's Name"] + "+0"
            if rel[sym_name]:
                p = rel[sym_name].split("+")
                if len(p) == 1:
                    p = ["", p[0]]
                rel[sym_name] = (p[0], int(p[1], 16))

    if toc is None:
        error(f"failed to analyze relocations in {f}")

    return rels


def collect_sections(f):
    """Collect section info from ELF."""

    # Check if this is a Mach-O file (macOS)
    try:
        file_out, _ = run(["file", f])
        if "Mach-O" in file_out:
            # Return empty list for Mach-O files - sections not supported yet
            return []
    except (OSError, subprocess.SubprocessError):
        pass

    out, _ = run(["readelf", "-SW", f])

    toc = None
    sections = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"\[\s+", "[", line)
        words = re.split(r" +", line)
        if line.startswith("[Nr]"):  # Header?
            if toc is not None:
                error("multiple headers in output of readelf")
            toc = make_toc(words, {"Addr": "Address"})
        elif line.startswith("[") and toc is not None:
            sec = parse_row(words, toc, ["Address", "Off", "Size"])
            if "A" in sec["Flg"]:  # Allocatable section?
                sections.append(sec)

    if toc is None:
        error(f"failed to analyze sections in {f}")

    return sections


def read_unrelocated_data(input_name, syms, secs):
    """Collect unrelocated data from ELF."""
    data = {}
    with open(input_name, "rb") as f:

        def is_symbol_in_section(sym, sec):
            sec_end = sec["Address"] + sec["Size"]
            is_start_in_section = sec["Address"] <= sym["Value"] < sec_end
            is_end_in_section = sym["Value"] + sym["Size"] <= sec_end
            return is_start_in_section and is_end_in_section

        for name, s in sorted(syms.items(), key=lambda s: s[1]["Value"]):
            # TODO: binary search (bisect)
            sec = [sec for sec in secs if is_symbol_in_section(s, sec)]
            if len(sec) != 1:
                error(
                    f"failed to locate section for interval "
                    f"[{s['Value']:x}, {s['Value'] + s['Size']:x})"
                )
            sec = sec[0]
            f.seek(sec["Off"])
            data[name] = f.read(s["Size"])
    return data


def collect_relocated_data(syms, bites, rels, ptr_size, reloc_types):
    """Identify relocations for each symbol"""
    data = {}
    for name, s in sorted(syms.items()):
        b = bites.get(name)
        assert b is not None
        if s["Demangled Name"].startswith("typeinfo name"):
            data[name] = [("byte", int(x)) for x in b]
            continue
        data[name] = []
        for i in range(0, len(b), ptr_size):
            val = int.from_bytes(
                b[i * ptr_size : (i + 1) * ptr_size], byteorder="little"
            )
            data[name].append(("offset", val))
        start = s["Value"]
        finish = start + s["Size"]
        # TODO: binary search (bisect)
        for rel in rels:
            if rel["Type"] in reloc_types and start <= rel["Offset"] < finish:
                i = (rel["Offset"] - start) // ptr_size
                assert i < len(data[name])
                data[name][i] = "reloc", rel
    return data


def generate_vtables(cls_tables, cls_syms, cls_data):
    """Generate code for vtables"""
    c_types = {"reloc": "const void *", "byte": "unsigned char", "offset": "size_t"}

    ss = []
    ss.append(
        """\
#ifdef __cplusplus
extern "C" {
#endif

"""
    )

    # Print externs

    printed = set()
    for name, data in sorted(cls_data.items()):
        for typ, val in data:
            if typ != "reloc":
                continue
            sym_name, addend = val["Symbol's Name + Addend"]
            sym_name = re.sub(r"@.*", "", sym_name)  # Can we pin version in C?
            if sym_name not in cls_syms and sym_name not in printed:
                ss.append(
                    f"""\
extern const char {sym_name}[];

"""
                )

    # Collect variable infos

    code_info = {}

    for name, s in sorted(cls_syms.items()):
        data = cls_data[name]
        if s["Demangled Name"].startswith("typeinfo name"):
            declarator = "const unsigned char %s[]"
        else:
            field_types = (
                f"{c_types[typ]} field_{i};" for i, (typ, _) in enumerate(data)
            )
            declarator = f"const struct {{ {' '.join(field_types)} }} %s"
        vals = []
        for typ, val in data:
            if typ != "reloc":
                vals.append(str(val) + "UL")
            else:
                sym_name, addend = val["Symbol's Name + Addend"]
                sym_name = re.sub(r"@.*", "", sym_name)  # Can we pin version in C?
                vals.append(f"(const char *)&{sym_name} + {addend}")
        code_info[name] = (
            declarator,
            f"{{ {', '.join(vals)} }}",
        )

    # Print declarations

    for name, (decl, _) in sorted(code_info.items()):
        type_name = name + "_type"
        type_decl = decl % type_name
        ss.append(
            f"""\
typedef {type_decl};
extern __attribute__((weak)) {type_name} {name};
"""
        )

    # Print definitions

    for name, (_, init) in sorted(code_info.items()):
        type_name = name + "_type"
        ss.append(
            f"""\
const {type_name} {name} = {init};
"""
        )

    ss.append(
        """\
#ifdef __cplusplus
}  // extern "C"
#endif
"""
    )

    return "".join(ss)


def read_soname(f):
    """Read ELF's SONAME."""

    # Check if this is a Mach-O file (macOS)
    try:
        file_out, _ = run(["file", f])
        if "Mach-O" in file_out:
            # For Mach-O files, return the filename as soname
            return os.path.basename(f)
    except (OSError, subprocess.SubprocessError):
        pass

    out, _ = run(["readelf", "-d", f])

    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # 0x000000000000000e (SONAME)             Library soname: [libndp.so.0]
        soname_match = re.search(r"\(SONAME\).*\[(.+)\]", line)
        if soname_match is not None:
            return soname_match[1]

    return None


def read_library_name(filename):
    """Read library name from .def file."""

    try:
        with open(filename, "r", encoding="utf-8") as f:
            for line in f.readlines():
                line = line.strip()
                m = re.match(r"^(?:LIBRARY|NAME)\s+([A-Za-z0-9_.\-]+)$", line)
                if m is not None:
                    return m[1]
    except (UnicodeDecodeError, IOError):
        # Not a text file, skip
        pass

    return None


def main():
    """Driver function"""
    parser = argparse.ArgumentParser(
        description="Generate wrappers for shared library functions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
Examples:
  $ python3 {me} /usr/lib/x86_64-linux-gnu/libaccountsservice.so.0
  Generating libaccountsservice.so.0.tramp.S...
  Generating libaccountsservice.so.0.init.c...
""",
    )

    parser.add_argument(
        "library",
        metavar="LIB",
        help="Library to be wrapped (or .def file with list of functions).",
    )
    parser.add_argument(
        "--verbose", "-v", help="Print diagnostic info", action="count", default=0
    )
    parser.add_argument(
        "--dlopen",
        help="Emit dlopen call (default)",
        dest="dlopen",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-dlopen",
        help="Do not emit dlopen call (user must load/unload library himself)",
        dest="dlopen",
        action="store_false",
    )
    parser.add_argument(
        "--dlopen-callback",
        help="Call user-provided custom callback to load library instead of dlopen",
        default="",
    )
    parser.add_argument(
        "--dlsym-callback",
        help="Call user-provided custom callback to resolve a symbol, "
        "instead of dlsym",
        default="",
    )
    parser.add_argument(
        "--library-load-name",
        help="Use custom name for dlopened library (default is SONAME)",
    )
    parser.add_argument(
        "--lazy-load",
        help="Load library on first call to any of it's functions (default)",
        dest="lazy_load",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-lazy-load",
        help="Load library at program start",
        dest="lazy_load",
        action="store_false",
    )
    parser.add_argument(
        "--thread-safe",
        help="Ensure thread-safety (default)",
        dest="thread_safe",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-thread-safe",
        help="Do not ensure thread-safety",
        dest="thread_safe",
        action="store_false",
    )
    parser.add_argument(
        "--vtables",
        help="Intercept virtual tables (EXPERIMENTAL)",
        dest="vtables",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--no-vtables",
        help="Do not intercept virtual tables (default)",
        dest="vtables",
        action="store_false",
    )
    parser.add_argument(
        "--no-weak-symbols",
        help="Don't bind weak symbols",
        dest="no_weak_symbols",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--target",
        help="Target platform triple e.g. x86_64-unknown-linux-gnu or arm-none-eabi "
        "(atm x86_64, i[0-9]86, arm/armhf/armeabi, aarch64/armv8, "
        "mips/mipsel, mips64/mip64el, e2k, powerpc64/powerpc64le, "
        "riscv64 are supported)",
        default=os.uname()[-1],
    )
    parser.add_argument(
        "--symbol-list",
        help="Path to file with symbols that should be present in wrapper "
        "(all by default)",
    )
    parser.add_argument(
        "--symbol-prefix",
        metavar="PFX",
        help="Prefix wrapper symbols with PFX",
        default="",
    )
    parser.add_argument(
        "-q", "--quiet", help="Do not print progress info", action="store_true"
    )
    parser.add_argument(
        "--outdir", "-o", help="Path to create wrapper at", default="./"
    )
    parser.add_argument(
        "--suffix", help="Specify a custom suffix for output files.", default=None
    )

    args = parser.parse_args()

    input_name = args.library
    verbose = args.verbose
    dlopen_callback = args.dlopen_callback
    dlsym_callback = args.dlsym_callback
    dlopen = args.dlopen
    lazy_load = args.lazy_load
    thread_safe = args.thread_safe
    if args.target.startswith("arm"):
        target = "arm"  # Handle armhf-..., armel-...
    elif re.match(r"^i[0-9]86", args.target):
        target = "i386"
    elif args.target.startswith("amd64"):
        target = "x86_64"
    elif args.target.startswith("mips64"):
        target = "mips64"  # Handle mips64-..., mips64el-..., mips64le-...
    elif args.target.startswith("mips"):
        target = "mips"  # Handle mips-..., mipsel-..., mipsle-...
    elif args.target.startswith("ppc64le"):
        target = "powerpc64le"
    elif args.target.startswith("ppc64"):
        target = "powerpc64"
    elif args.target.startswith("rv64"):
        target = "riscv64"
    else:
        target = args.target.split("-")[0]
    quiet = args.quiet
    outdir = args.outdir
    if not os.path.exists(outdir):
        os.makedirs(outdir)

    if not os.path.exists(outdir):
        os.makedirs(outdir)

    if args.symbol_list is None:
        funs = None
    else:
        with open(args.symbol_list, "r") as f:
            funs = []
            for line in re.split(r"\r?\n", f.read()):
                line = re.sub(r"#.*", "", line)
                line = line.strip()
                if line:
                    funs.append(line)

    binary = is_binary_file(input_name)
    stem = os.path.basename(input_name)
    if not binary:
        stem = re.sub(r"\.def$", "", stem)

    if args.library_load_name is not None:
        load_name = args.library_load_name
    elif binary:
        load_name = read_soname(input_name)
        if load_name is None:
            load_name = stem
    else:
        load_name = read_library_name(input_name)
        if load_name is None:
            load_name = stem

    # Collect target info

    target_dir = os.path.join(root, "arch", target)

    if not os.path.exists(target_dir):
        error(f"unknown architecture '{target}'")

    cfg = configparser.ConfigParser(inline_comment_prefixes=";")
    cfg.read(target_dir + "/config.ini")

    ptr_size = int(cfg["Arch"]["PointerSize"])
    symbol_reloc_types = set(re.split(r"\s*,\s*", cfg["Arch"]["SymbolReloc"]))

    def is_exported(s):
        conditions = [
            s["Bind"] != "LOCAL",
            s["Visibility"] != "HIDDEN",
            s["Type"] != "NOTYPE",
            s["Ndx"] != "UND",
            s["Name"] not in ["", "_init", "_fini"],
        ]
        if args.no_weak_symbols:
            conditions.append(s["Bind"] != "WEAK")
        return all(conditions)

    if binary:
        syms = collect_syms(input_name)
    else:
        syms = collect_def_exports(input_name)

    # Also collected demangled names
    if syms:
        out, _ = run(["c++filt"], "\n".join((sym["Name"] for sym in syms)))
        out = out.rstrip("\n")  # Some c++filts append newlines at the end
        for i, name in enumerate(out.split("\n")):
            syms[i]["Demangled Name"] = name

    syms = list(filter(is_exported, syms))

    def is_data_symbol(s):
        return (
            s["Type"] == "OBJECT"
            # Allow vtables if --vtables is on
            and not (" for " in s["Demangled Name"] and args.vtables)
        )

    exported_data = [s["Name"] for s in syms if is_data_symbol(s)]
    if exported_data:
        # TODO: we can generate wrappers for const data without relocations
        # (or only code relocations)
        warn(
            f"library '{input_name}' contains data symbols which won't be "
            f"intercepted: {', '.join(exported_data)}"
        )

    # Collect functions
    # TODO: warn if user-specified functions are missing

    orig_funs = filter(lambda s: s["Type"] == "FUNC", syms)

    all_funs = set()
    warn_versioned = False
    for s in orig_funs:
        if not s["Default"]:
            # TODO: support versions
            if not warn_versioned:
                warn(f"library {input_name} contains versioned symbols which are NYI")
                warn_versioned = True
            if verbose:
                print(f"Skipping versioned symbol {s['Name']}")
            continue
        all_funs.add(s["Name"])

    if funs is None:
        funs = sorted(list(all_funs))
        if not funs and not quiet:
            warn(f"no public functions were found in {input_name}")
    else:
        missing_funs = [name for name in funs if name not in all_funs]
        if missing_funs:
            warn(
                "some user-specified functions are not present in library: "
                + ", ".join(missing_funs)
            )
        funs = [name for name in funs if name in all_funs]

    if verbose:
        print("Exported functions:")
        for i, fun in enumerate(funs):
            print(f"  {i}: {fun}")

    # Collect vtables

    if args.vtables:
        if not binary and input_name.endswith(".def"):
            error("vtables not supported for .def files")

        cls_tables = {}
        cls_syms = {}

        for s in syms:
            m = re.match(
                r"^(vtable|typeinfo|typeinfo name) for (.*)", s["Demangled Name"]
            )
            if m is not None and is_exported(s):
                typ, cls = m.groups()
                name = s["Name"]
                cls_tables.setdefault(cls, {})[typ] = name
                cls_syms[name] = s

        if verbose:
            print("Exported classes:")
            for cls, _ in sorted(cls_tables.items()):
                print(f"  {cls}")

        secs = collect_sections(input_name)
        if verbose:
            print("Sections:")
            for sec in secs:
                print(
                    f"  {sec['Name']}: [{sec['Address']:x}, "
                    f"{sec['Address'] + sec['Size']:x}), at {sec['Off']:x}"
                )

        bites = read_unrelocated_data(input_name, cls_syms, secs)

        rels = collect_relocs(input_name)
        if verbose:
            print("Relocs:")
            for rel in rels:
                sym_add = rel["Symbol's Name + Addend"]
                print(f"  {rel['Offset']}: {sym_add}")

        cls_data = collect_relocated_data(
            cls_syms, bites, rels, ptr_size, symbol_reloc_types
        )
        if verbose:
            print("Class data:")
            for name, data in sorted(cls_data.items()):
                demangled_name = cls_syms[name]["Demangled Name"]
                print(f"  {name} ({demangled_name}):")
                for typ, val in data:
                    print(
                        "    "
                        + str(val if typ != "reloc" else val["Symbol's Name + Addend"])
                    )

    # Generate assembly code

    if args.suffix is not None:
        suffix = args.suffix
    else:
        suffix = os.path.basename(input_name)
        if not binary:
            suffix = re.sub(r"\.def$", "", suffix)
    lib_suffix = re.sub(r"[^a-zA-Z_0-9]+", "_", suffix)

    tramp_file = f"{suffix}.tramp.S"
    with open(os.path.join(outdir, tramp_file), "w") as f:
        if not quiet:
            print(f"Generating {tramp_file}...")
        with open(target_dir + "/table.S.tpl", "r") as t:
            table_text = string.Template(t.read()).substitute(
                lib_suffix=lib_suffix, table_size=ptr_size * (len(funs) + 1)
            )
        f.write(table_text)

        with open(target_dir + "/trampoline.S.tpl", "r") as t:
            tramp_tpl = string.Template(t.read())

        for i, name in enumerate(funs):
            tramp_text = tramp_tpl.substitute(
                lib_suffix=lib_suffix,
                sym=args.symbol_prefix + name,
                offset=i * ptr_size,
                number=i,
            )
            f.write(tramp_text)

    # Generate C code

    init_file = f"{suffix}.init.c"
    with open(os.path.join(outdir, init_file), "w") as f:
        if not quiet:
            print(f"Generating {init_file}...")
        with open(os.path.join(root, "arch/common/init.c.tpl"), "r") as t:
            if funs:
                sym_names = ",\n  ".join(f'"{name}"' for name in funs) + ","
            else:
                sym_names = ""
            init_text = string.Template(t.read()).substitute(
                lib_suffix=lib_suffix,
                load_name=load_name,
                dlopen_callback=dlopen_callback,
                dlsym_callback=dlsym_callback,
                has_dlopen_callback=int(bool(dlopen_callback)),
                has_dlsym_callback=int(bool(dlsym_callback)),
                no_dlopen=int(not dlopen),
                lazy_load=int(lazy_load),
                thread_safe=int(thread_safe),
                sym_names=sym_names,
            )
            f.write(init_text)
        if args.vtables:
            vtable_text = generate_vtables(cls_tables, cls_syms, cls_data)
            f.write(vtable_text)


if __name__ == "__main__":
    main()
