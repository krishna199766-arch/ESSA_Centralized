"""Run every backend check.

    python backend/tools/run_tests.py

There is no pytest here and no test package — each check is a script that builds
its own throwaway SQLite database and asserts in plain prose, because that is
what the rest of this repo does (android/tools, frontend/tools) and a second
convention would only mean two things to remember.

This exists so there is ONE thing to run before pushing rather than a list to
recall. It picks up *_test.py beside it, so a check added tomorrow is included by
being written, not by being registered here.

The interpreter matters: these import the app, so they need the environment that
has its dependencies. On the warehouse PC that is backend/.venv — run this with
that python, not the one first on PATH.
"""
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def main():
    scripts = sorted(p for p in HERE.glob("*_test.py"))
    if not scripts:
        print("No *_test.py beside %s" % HERE)
        return 0

    width = max(len(p.name) for p in scripts)
    failed = []
    for path in scripts:
        print("\n" + "=" * 72)
        print("== %s" % path.name)
        print("=" * 72)
        # cwd is the repo root: the scripts put it on sys.path themselves, but a
        # relative DATABASE_URL or data file would resolve from wherever this was
        # invoked, and "it passes from one directory" is not a passing test
        rc = subprocess.call([sys.executable, str(path)], cwd=str(ROOT))
        if rc != 0:
            failed.append(path.name)

    print("\n" + "=" * 72)
    for path in scripts:
        print("  %-*s  %s" % (width, path.name,
                              "FAILED" if path.name in failed else "ok"))
    print("=" * 72)
    if failed:
        print("\n%d of %d FAILED" % (len(failed), len(scripts)))
        return 1
    print("\nall %d passing" % len(scripts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
