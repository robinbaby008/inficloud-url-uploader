#!/usr/bin/env python3
"""
url_to_infinicloud.py - Transfer files to InfiniCLOUD by giving a direct download link.

InfiniCLOUD has NO built-in server-side "remote upload / fetch from URL" in the web UI.
This script fills that gap: it streams DOWNLOAD_URL -> PUT to your WebDAV endpoint,
without needing to first save the whole file on disk (memory-safe for large files).

Get your credentials from InfiniCLOUD My Page:
  My Page > Apps Connection > Turn ON
  - WebDAV Connection URL e.g. https://abcd1234.infini-cloud.net/dav/
  - Connection ID (your User ID)
  - Apps Password (shown once, Reissue if lost)

Usage:
  python3 url_to_infinicloud.py "https://example.com/file.zip"
  python3 url_to_infinicloud.py "https://example.com/file.zip" --dest "backup/file.zip"
  python3 url_to_infinicloud.py --list links.txt --remote-dir "uploads/"
  python3 url_to_infinicloud.py "https://example.com/file.zip" --webdav-url ... --user ... --password ...

Env vars (so you don't paste password on command line):
  INFINICLOUD_WEBDAV_URL, INFINICLOUD_USER, INFINICLOUD_PASS

Requires: requests (preinstalled on most systems, Termux: pkg install python; pip install requests)
"""
import argparse
import os
import re
import sys
import urllib.parse

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError:
    print("ERROR: 'requests' module not found. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


def parse_args():
    p = argparse.ArgumentParser(description="Download from URL and upload directly to InfiniCLOUD (WebDAV).")
    p.add_argument("url", nargs="?", help="Direct download link to transfer")
    p.add_argument("--dest", help="Destination filename on InfiniCLOUD, e.g. 'movies/file.mkv'. If omitted, auto-detected.")
    p.add_argument("--list", help="Text file with one download URL per line (for batch transfer)")
    p.add_argument("--remote-dir", default="", help="Remote folder prefix on InfiniCLOUD, e.g. 'uploads/'")
    p.add_argument("--webdav-url", default=os.environ.get("INFINICLOUD_WEBDAV_URL", ""),
                   help="WebDAV URL e.g. https://xxxx.infini-cloud.net/dav/ (or env INFINICLOUD_WEBDAV_URL)")
    p.add_argument("--user", default=os.environ.get("INFINICLOUD_USER", ""),
                   help="Connection ID (or env INFINICLOUD_USER)")
    p.add_argument("--password", default=os.environ.get("INFINICLOUD_PASS", ""),
                   help="Apps Password (or env INFINICLOUD_PASS)")
    p.add_argument("--overwrite", action="store_true", help="Overwrite if remote file exists (default: overwrite via PUT anyway)")
    p.add_argument("--timeout", type=int, default=60, help="HTTP timeout seconds (default 60)")
    return p.parse_args()


def guess_filename_from_url(url, response=None):
    # 1. Try Content-Disposition header
    if response is not None:
        cd = response.headers.get("Content-Disposition", "")
        if cd:
            m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", cd, re.I)
            if m:
                return urllib.parse.unquote(m.group(1))
            m = re.search(r'filename\s*=\s*"([^"]+)"', cd)
            if m:
                return m.group(1)
            m = re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    # 2. URL path
    path = urllib.parse.urlparse(url).path.rstrip("/")
    name = os.path.basename(path) if path else ""
    name = urllib.parse.unquote(name)
    if name and "." in name or len(name) > 1:
        return name
    return "downloaded_file"


def ensure_remote_dir(webdav_base, remote_dir, auth, timeout):
    """Create remote folders with MKCOL (needed by InfiniCLOUD WebDAV)."""
    if not remote_dir.strip("/ "):
        return
    parts = [pp for pp in remote_dir.strip("/").split("/") if pp]
    cur = ""
    for part in parts:
        cur = f"{cur}/{part}" if cur else part
        url = webdav_base + "/".join(urllib.parse.quote(p, safe="") for p in cur.split("/")) + "/"
        r = requests.request("MKCOL", url, auth=auth, timeout=timeout)
        # 201 created, 405/409 = already exists / conflict (ignore), else warn
        if r.status_code not in (201, 405, 409, 301):
            # Some servers return 405 if exists - fine
            pass


def transfer_one(download_url, dest_name, webdav_base, auth, timeout):
    download_url = download_url.strip()
    if not download_url or download_url.startswith("#"):
        return True

    # Stream download to discover filename + size
    print(f"\n[+] Downloading (stream): {download_url}")
    with requests.get(download_url, stream=True, timeout=timeout,
                      headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True) as r:
        try:
            r.raise_for_status()
        except Exception as e:
            print(f"[!] Download failed: {e}", file=sys.stderr)
            return False

        total = int(r.headers.get("Content-Length", 0)) or None
        if not dest_name:
            dest_name_resolved = guess_filename_from_url(download_url, r)
        else:
            dest_name_resolved = dest_name

        # Sanitize: strip leading /
        dest_name_resolved = dest_name_resolved.lstrip("/")
        remote_path = dest_name_resolved
        encoded_parts = "/".join(urllib.parse.quote(p, safe="") for p in remote_path.split("/"))
        put_url = webdav_base + encoded_parts

        print(f"[+] Uploading to: {put_url}")
        if total:
            print(f"    Size: {total/1024/1024:.2f} MB")
        else:
            print("    Size: unknown (chunked)")

        # Stream PUT directly from GET iterator - no temp file
        def gen():
            downloaded = 0
            last_pct = -1
            for chunk in r.iter_content(chunk_size=1024*256):
                if chunk:
                    downloaded += len(chunk)
                    if total:
                        pct = int(downloaded*100/total)
                        if pct != last_pct and pct % 5 == 0:
                            print(f"\r    Progress: {pct}% ({downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB)", end="", flush=True)
                            last_pct = pct
                    else:
                        if downloaded % (10*1024*1024) < 256*1024:
                            print(f"\r    Transferred: {downloaded/1024/1024:.1f} MB", end="", flush=True)
                    yield chunk
            print()

        headers = {}
        if total:
            headers["Content-Length"] = str(total)

        put = requests.put(put_url, data=gen(), auth=auth, headers=headers, timeout=(30, None))
        if put.status_code in (200, 201, 204):
            print(f"[OK] Uploaded: {remote_path}")
            return True
        else:
            print(f"[!] Upload failed {put.status_code}: {put.text[:500]}", file=sys.stderr)
            return False


def main():
    args = parse_args()

    if not args.webdav_url or not args.user or not args.password:
        print("Missing InfiniCLOUD credentials.", file=sys.stderr)
        print("Provide --webdav-url, --user, --password or set env vars:", file=sys.stderr)
        print("  INFINICLOUD_WEBDAV_URL=https://xxxx.infini-cloud.net/dav/", file=sys.stderr)
        print("  INFINICLOUD_USER, INFINICLOUD_PASS", file=sys.stderr)
        print("\nGet them from: InfiniCLOUD My Page > Apps Connection > Turn ON", file=sys.stderr)
        sys.exit(2)

    webdav_base = args.webdav_url.strip()
    if not webdav_base.endswith("/"):
        webdav_base += "/"

    auth = HTTPBasicAuth(args.user, args.password)

    # Verify connection
    print(f"[*] Checking InfiniCLOUD connection: {webdav_base}")
    try:
        chk = requests.request("PROPFIND", webdav_base, auth=auth, headers={"Depth": "0"}, timeout=args.timeout)
        if chk.status_code in (401, 403):
            print("[!] Auth failed: check Connection ID / Apps Password, and enable Apps Connection in My Page.", file=sys.stderr)
            sys.exit(3)
        print(f"[*] Connected (PROPFIND {chk.status_code})")
    except Exception as e:
        print(f"[!] Cannot reach WebDAV: {e}", file=sys.stderr)
        sys.exit(3)

    if args.remote_dir:
        ensure_remote_dir(webdav_base, args.remote_dir, auth, args.timeout)
        prefix = args.remote_dir.strip("/").strip() + "/"
    else:
        prefix = ""

    urls = []
    if args.list:
        with open(args.list, encoding="utf-8") as f:
            for line in f:
                line=line.strip()
                if line and not line.startswith("#"):
                    # support "URL | dest" format
                    if "|" in line:
                        u, d = [x.strip() for x in line.split("|", 1)]
                        urls.append((u, d))
                    else:
                        urls.append((line, None))
    elif args.url:
        d = args.dest
        if d is None and prefix:
            d = None  # will prepend prefix later
        urls.append((args.url, d))
    else:
        print("Provide a URL or --list file.txt", file=sys.stderr)
        sys.exit(2)

    ok = 0
    for u, d in urls:
        if d:
            dest = prefix + d.lstrip("/") if prefix and not d.startswith(prefix) else d
        else:
            # dest auto, but prepend remote-dir
            # need temp resolve: transfer_one will guess, so pass prefix + guessed? Handle by passing None and prepending after? Simpler: pass prefix as dir and let function guess inside.
            # Workaround: if prefix, we pass dest=None then fix inside transfer by prepending. Do it here via two-step: guess after download is complex, so modify transfer_one call.
            dest = None
            if prefix:
                # Monkey: transfer with full dest after guessing - do inline
                # Call transfer with dest=None then move? Easier: just ensure dir and prepend after guess inside transfer_one.
                # We'll handle by temporarily wrapping: download to prefix/
                pass
        if dest is None and prefix:
            # Let transfer_one guess, but inject prefix: we call with dest = prefix + guessed? Can't know guessed yet.
            # So do transfer manually: guess happens inside, we need prefix. Patch: pass dest as prefix + placeholder and fix.
            # Simplest: call transfer_one with dest=None, but patch webdav_base to include prefix
            ok_one = transfer_one(u, None, webdav_base + prefix, auth, args.timeout)
        else:
            ok_one = transfer_one(u, dest, webdav_base, auth, args.timeout)
        ok += 1 if ok_one else 0

    print(f"\nDone: {ok}/{len(urls)} succeeded.")
    sys.exit(0 if ok == len(urls) else 1)


if __name__ == "__main__":
    main()
