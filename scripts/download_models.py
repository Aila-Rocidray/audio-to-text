"""Download faster-whisper CT2 models from ModelScope into workspace-local dirs."""

import json
import os
import sys
import time
import urllib.request

MODELS = os.environ.get("AUDIO2TEXT_MODELS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models"))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

REPOS = {
    "large-v3": "pengzhendong/faster-whisper-large-v3",
    "medium.en": "pengzhendong/faster-whisper-medium.en",
}

SKIP = {".gitattributes", "README.md", "configuration.json"}


def file_url(repo, path):
    return (
        f"https://modelscope.cn/api/v1/models/{repo}/repo"
        f"?Revision=master&FilePath={urllib.parse.quote(path)}"
    )


def list_files(repo):
    url = f"https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Root=/"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        data = json.loads(r.read())
    return data["Data"]["Files"]


def download(url, dest):
    tmp = dest + ".part"
    t0 = time.time()
    n = 0
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            n += len(chunk)
            if total and n % (64 << 20) < (1 << 20):
                dt = max(time.time() - t0, 1e-6)
                print(f"    {n/1e6:7.1f}/{total/1e6:.1f} MB  {n/dt/1e6:5.1f} MB/s", flush=True)
    os.replace(tmp, dest)
    dt = time.time() - t0
    print(f"  done {os.path.basename(dest)} {n/1e6:.1f} MB in {dt:.1f}s ({n/dt/1e6:.1f} MB/s)", flush=True)


def main():
    os.makedirs(MODELS, exist_ok=True)
    for name, repo in REPOS.items():
        out = os.path.join(MODELS, name)
        os.makedirs(out, exist_ok=True)
        print(f"== {name} <- {repo}", flush=True)
        files = list_files(repo)
        for f in files:
            path = f["Path"]
            size = f["Size"]
            if path in SKIP:
                continue
            dest = os.path.join(out, path)
            if os.path.exists(dest) and os.path.getsize(dest) == size:
                print(f"  skip {path} (exists)", flush=True)
                continue
            print(f"  fetching {path} ({size/1e6:.1f} MB)", flush=True)
            download(file_url(repo, path), dest)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    import urllib.parse  # noqa: F401  (used in file_url)

    main()
