"""
Download the llama-cpp-python sdist and extract it WITHOUT the long-path
'webui' subtree (which exceeds Windows MAX_PATH and isn't needed to build the
Python bindings). Prints the extracted package directory path on success.

    python scripts/fetch_llama_src.py
"""
import io
import os
import sys
import tarfile
import urllib.request

VERSION = "0.3.23"
DEST = r"C:\t"  # short dir to keep paths under MAX_PATH
PKG_DIR = os.path.join(DEST, f"llama_cpp_python-{VERSION}")
TARBALL = os.path.join(DEST, f"llama_cpp_python-{VERSION}.tar.gz")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

os.makedirs(DEST, exist_ok=True)


def sdist_url():
    import json
    api = f"https://pypi.org/pypi/llama-cpp-python/{VERSION}/json"
    with urllib.request.urlopen(api, timeout=60) as r:
        data = json.load(r)
    for f in data["urls"]:
        if f["packagetype"] == "sdist":
            return f["url"]
    raise RuntimeError("Khong tim thay sdist url")


def download():
    if os.path.exists(TARBALL) and os.path.getsize(TARBALL) > 60_000_000:
        print(f"Da co tarball: {TARBALL} ({os.path.getsize(TARBALL)} bytes)", flush=True)
        return
    url = sdist_url()
    print(f"Tai: {url}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(TARBALL, "wb") as out:
        total = int(r.headers.get("Content-Length", 0))
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r  {got/1e6:.1f}/{total/1e6:.1f} MB", end="", flush=True)
    print("\nTai xong.", flush=True)


def extract():
    skipped = 0
    with tarfile.open(TARBALL, "r:gz") as tar:
        members = []
        for m in tar.getmembers():
            # Drop the deeply-nested server web UI assets (not needed to build).
            if "/webui/" in m.name or m.name.endswith("/webui"):
                skipped += 1
                continue
            members.append(m)
        tar.extractall(DEST, members=members)
    print(f"Giai nen xong (bo qua {skipped} file webui).", flush=True)


download()
extract()
if os.path.isdir(PKG_DIR):
    print("PKG_DIR=" + PKG_DIR, flush=True)
else:
    print("LOI: khong thay thu muc package sau giai nen", flush=True)
    sys.exit(1)
