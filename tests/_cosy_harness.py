# Adapted from pals2cosy, copyright (c) 2026 Eremey Valetov.
# MIT licence: ../palsparserpy/COSY_COPYING.txt.
"""Run generated COSYScript through COSY INFINITY and read back map coefficients.

Tests using this module are skipped when COSY INFINITY is not installed.
"""

import os
import re
import shutil
import subprocess

import pytest

COSY_BIN = os.environ.get("PALS_COSY_BIN", "/usr/local/bin/cosy")
COSY_LIBRARY = os.environ.get("PALS_COSY_LIBRARY", "/usr/local/bin/cosy.fox")
# Fringe-field data that COSY reads for FR 1 and FR 2.
COSY_SYSCA = os.environ.get("PALS_COSY_SYSCA",
                            os.path.join(os.path.dirname(COSY_LIBRARY), "SYSCA.DAT"))

requires_cosy = pytest.mark.skipif(
    not (os.path.exists(COSY_BIN) and os.path.exists(COSY_LIBRARY)),
    reason="COSY INFINITY binary or cosy.fox not found",
)
requires_sysca = pytest.mark.skipif(
    not os.path.exists(COSY_SYSCA),
    reason="SYSCA.DAT not found (needed by FR 1 and 2); set PALS_COSY_SYSCA",
)

_MAP_PROBE = """\
    LATTICE ;
    WRITE 6 '### SPOS' ; WRITE 6 SPOS ;
    WRITE 6 '### E0' ; WRITE 6 CONS(E0) ;
    WRITE 6 '### CHIM' ; WRITE 6 CONS(CHIM) ;
    WRITE 6 '### MAP' ;
    WRITE 6 MAP(1) ; WRITE 6 MAP(2) ; WRITE 6 MAP(3) ;
    WRITE 6 MAP(4) ; WRITE 6 MAP(5) ; WRITE 6 MAP(6) ;
"""

_COEFF_RE = re.compile(r"^\s*\d+\s+(\S+)\s+\d+\s+([\d ]+)$")


def compile_cosy(workdir):
    """Copy COSY (and SYSCA.DAT, if found) into ``workdir`` and compile its
    library once."""
    shutil.copy2(COSY_BIN, os.path.join(workdir, "cosy"))
    os.chmod(os.path.join(workdir, "cosy"), 0o755)
    shutil.copy2(COSY_LIBRARY, os.path.join(workdir, "cosy.fox"))
    if os.path.exists(COSY_SYSCA):
        shutil.copy2(COSY_SYSCA, os.path.join(workdir, "SYSCA.DAT"))
    subprocess.run(["./cosy", "cosy.fox"], cwd=workdir, stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=120, check=True)
    return workdir


class CosyResult:
    def __init__(self, stdout, stderr="", returncode=0):
        self.stdout = stdout
        self.errors = [line for line in (stdout + "\n" + stderr).splitlines()
                       if re.search(r"\*\*\* [A-Z]|\$\$\$|!!!|ERROR|forrtl", line)]
        if returncode:
            self.errors.append(f"COSY exited with status {returncode}")
        self.scalars = {}
        self.map = []
        tag, vec = None, []
        for line in stdout.splitlines():
            if line.startswith("### "):
                tag = line[4:].strip()
                continue
            if tag in ("SPOS", "E0", "CHIM") and line.strip() and tag not in self.scalars:
                self.scalars[tag] = float(line.split()[0].replace("D", "E"))
                continue
            if tag == "MAP":
                if line.strip().startswith("----"):
                    self.map.append(vec)
                    vec = []
                    continue
                m = _COEFF_RE.match(line)
                if m:
                    exps = tuple(int(x) for x in m.group(2).split())
                    vec.append((float(m.group(1)), exps))

    def coeff(self, row, *exponents):
        """Map coefficient of output ``row`` (0 = x, 1 = a, 2 = y, 3 = b,
        4 = l, 5 = delta) for the monomial with ``exponents``."""
        target = tuple(exponents) + (0,) * (6 - len(exponents))
        for value, exps in self.map[row]:
            if exps[:6] == target:
                return value
        return 0.0


def run_cosy(script, workdir, name="probe.fox", timeout=60):
    """Run a COSYScript programme from ``pals_to_cosy`` and return a :class:`CosyResult`."""
    if "    LATTICE ;\n" not in script:
        raise ValueError("COSYScript has no LATTICE call to instrument")
    path = os.path.join(workdir, name)
    with open(path, "w") as f:
        f.write(script.replace("    LATTICE ;\n", _MAP_PROBE, 1))
    proc = subprocess.run(["./cosy", name], cwd=workdir, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout)
    return CosyResult(proc.stdout, proc.stderr, proc.returncode)
