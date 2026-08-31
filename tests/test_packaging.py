"""The built wheel must ship the package's data files.

The Python modules alone are not the program: the viewer serves
``eclipse/static/viewer.html`` and the interface strings live in
``eclipse/langues/*.json``. Setuptools only bundles ``.py`` files unless
``package-data`` says otherwise, and an editable install (``pip install -e``,
the development setup) reads the source tree directly — so a missing
declaration stays invisible in development and breaks every normal
``pip install``: the server starts, then answers the very first request
with an empty response (FileNotFoundError on the page). This is exactly
what a first user reported (Chrome's ERR_EMPTY_RESPONSE on 127.0.0.1).

Building the wheel is the only honest check: inspecting the source tree
would always pass, and inspecting the *installed* package would test the
development install, not what users get.
"""
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Everything a wheel build needs, and nothing else. pyproject.toml names
#: README.md and LICENSE, so they are part of the build inputs.
BUILD_INPUTS = ("pyproject.toml", "README.md", "LICENSE", "eclipse")

#: Every non-Python file the program reads at run time. A new resource
#: added to the package belongs here, so the wheel build cannot silently
#: drop it again.
DATA_FILES = (
    "eclipse/static/viewer.html",
    "eclipse/langues/en.json",
    "eclipse/langues/fr.json",
)


@pytest.fixture(scope="module")
def wheel_entries(tmp_path_factory):
    """Names of all files inside a freshly built wheel.

    Built once for the module: the build takes seconds, the assertions
    do not need more than one wheel. Built from a COPY of the sources in
    tmp_path, not from the repository: setuptools writes its egg-info
    into the directory it builds, and the repository must stay untouched
    by a test run.
    """
    src = tmp_path_factory.mktemp("src")
    for name in BUILD_INPUTS:
        origin = os.path.join(_ROOT, name)
        if os.path.isdir(origin):
            shutil.copytree(origin, src / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(origin, src / name)
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        (sys.executable, "-m", "pip", "wheel", "--no-deps",
         "--wheel-dir", str(out), str(src)),
        check=True, capture_output=True, text=True)
    (wheel,) = out.glob("*.whl")
    with zipfile.ZipFile(wheel) as z:
        return z.namelist()


@pytest.mark.parametrize("data_file", DATA_FILES)
def test_the_wheel_ships_the_data_file(wheel_entries, data_file):
    assert data_file in wheel_entries
