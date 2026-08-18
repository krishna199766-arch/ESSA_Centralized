"""A rough structural check on MainActivity, for when there is no JDK about.

    python android/tools/java_shape_check.py

Not a compiler and not pretending to be one. It answers the two questions that
are worth answering before pushing a change to CI — do the braces balance, and
did every piece of the file-chooser plumbing actually land — because a build
that fails on a missing brace costs the same round trip as one that fails on
something interesting, and this one is free.
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "android", "app", "src", "main", "java",
                   "com", "essa", "warehouse", "MainActivity.java")

src = io.open(SRC, encoding="utf-8").read()

# Strip string literals and comments so the brackets inside them do not count.
stripped = re.sub(r'"(\\.|[^"\\])*"', '""', src)
stripped = re.sub(r"//[^\n]*", "", stripped)
stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)

fails = 0


def check(ok, label, detail=""):
    global fails
    if ok:
        print(f"  ok    {label}")
    else:
        fails += 1
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


print("brackets balance:")
for open_c, close_c, name in (("{", "}", "braces"), ("(", ")", "parentheses")):
    a, b = stripped.count(open_c), stripped.count(close_c)
    check(a == b, f"{name} ({a} open, {b} close)")

print("\nthe file-chooser plumbing is all present:")
for symbol, why in [
    ("onShowFileChooser", "without it the page's file input is silently dropped"),
    ("fileChooserLauncher", "the result has to come back somewhere"),
    ("onPermissionRequest", "getUserMedia is denied by default in a WebView"),
    ("buildChooser", "camera and picker in one dialog"),
    ("cameraIntent", "the camera needs somewhere to write"),
    ("FileProvider.getUriForFile", "a raw File:// uri is refused on modern Android"),
]:
    check(symbol in src, symbol, why)

print("\nthe callback is answered on every path:")
# A WebView whose chooser never answers refuses to open another one, so the
# callback must be consumed exactly once — set in one place, cleared in the
# handler, and called whatever the result was.
check(src.count("fileCallback = callback") == 1, "callback stored exactly once")
check(src.count("cb.onReceiveValue(out)") == 1, "callback always invoked with the result")
check("fileCallback.onReceiveValue(null)" in src,
      "a chooser opened over another one releases the first")

print("\nimports for everything used:")
for cls in ["android.net.Uri", "android.provider.MediaStore", "android.webkit.ValueCallback",
            "android.webkit.PermissionRequest", "androidx.core.content.FileProvider",
            "androidx.core.content.ContextCompat", "java.io.File",
            "androidx.activity.result.contract.ActivityResultContracts"]:
    check(f"import {cls};" in src, cls)

print("\nthe manifest declares the provider:")
manifest = io.open(os.path.join(ROOT, "android", "app", "src", "main",
                                "AndroidManifest.xml"), encoding="utf-8").read()
check("androidx.core.content.FileProvider" in manifest, "provider registered")
check("${applicationId}.fileprovider" in manifest,
      "authority matches getPackageName() + \".fileprovider\"")
check("@xml/file_paths" in manifest, "paths resource referenced")
check(os.path.exists(os.path.join(ROOT, "android", "app", "src", "main",
                                  "res", "xml", "file_paths.xml")),
      "res/xml/file_paths.xml exists")

print("" if not fails else "")
print(f"{fails} failing" if fails else "all passing")
sys.exit(1 if fails else 0)
