#!/usr/bin/env python3
"""
app.py - Web UI to transfer files to InfiniCLOUD via direct download link.

Run:
  python3 -u app.py --port 8000
Open:
  http://127.0.0.1:8000

Backend proxies: Browser -> localhost -> download to temp file -> WebDAV PUT
with known Content-Length (fixes BrokenPipe/chunked-encoding issues on InfiniCLOUD).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError:
    raise SystemExit("ERROR: 'requests' required. Install: pip install requests")

JOBS = {}
JOBS_LOCK = threading.Lock()

PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>InfiniCLOUD URL Uploader</title>
<style>
body{font-family:'NType 82','NType 82 Mono','NDot',monospace;max-width:640px;margin:24px auto;padding:0 16px;background:#000;color:#33ff33}
h2,h3{font-family:'NDot','NType 82',monospace;letter-spacing:2px;text-shadow:0 0 8px #33ff33}
.card{background:#000;border:1px solid #33ff33;border-radius:8px;padding:16px;margin-bottom:16px;box-shadow:0 0 12px rgba(51,255,51,.25)}
label{display:block;font-size:13px;margin:10px 0 4px;color:#33ff33;font-family:'NType 82','NType 82 Mono',monospace}
input{width:100%;padding:10px;background:#000;color:#33ff33;border:1px solid #33ff33;border-radius:4px;font-size:15px;box-sizing:border-box;font-family:'NType 82 Mono','NType 82',monospace;caret-color:#33ff33}
input::placeholder{color:#1a7a1a}
input:focus{outline:none;box-shadow:0 0 8px #33ff33}
.row{display:flex;gap:8px;margin-top:12px}
button{padding:10px 16px;background:#000;color:#33ff33;border:1px solid #33ff33;border-radius:4px;font-size:15px;cursor:pointer;font-family:'NDot','NType 82',monospace;letter-spacing:1px;text-shadow:0 0 6px #33ff33}
button:hover{background:#33ff33;color:#000;text-shadow:none}
#testBtn{background:#000}
#uploadBtn{background:#000;flex:1;font-weight:bold}
#uploadBtn:hover{background:#33ff33;color:#000}
button:disabled{opacity:.4}
#status,#testStatus{font-size:14px;margin-top:10px;white-space:pre-wrap;font-family:'NType 82 Mono',monospace}
.ok{color:#33ff33}.err{color:#ff4444}
small{color:#33ff33;opacity:.8}
.bar{height:12px;background:#031003;border:1px solid #33ff33;border-radius:4px;overflow:hidden;margin-top:8px}
.bar>div{height:100%;width:0%;background:#33ff33;box-shadow:0 0 8px #33ff33;transition:width .3s}
.log{background:#000;color:#33ff33;border:1px solid #33ff33;padding:10px;border-radius:4px;font-size:12px;max-height:200px;overflow:auto;margin-top:8px;white-space:pre-wrap;font-family:'NType 82 Mono',monospace}
</style>
</head>
<body>
<h2>InfiniCLOUD URL Uploader</h2>
<div class="card">
<h3>1. InfiniCLOUD connection</h3>
<small>My Page &gt; Apps Connection &gt; Turn ON to get these.</small>
<label>INFINICLOUD_WEBDAV_URL</label>
<input id="webdav_url" placeholder="https://xxxx.infini-cloud.net/dav/" />
<label>INFINICLOUD_USER (Connection ID)</label>
<input id="user" placeholder="your user id" />
<label>INFINICLOUD_PASS (Apps Password)</label>
<input id="password" type="password" placeholder="apps password" />
<div class="row">
<button id="testBtn">Test Connection</button>
</div>
<div id="testStatus"></div>
</div>

<div class="card">
<h3>2. Upload from link</h3>
<label>Download link (direct link)</label>
<input id="download_url" placeholder="https://example.com/file.zip" />
<label>Save as on InfiniCLOUD (optional, e.g. uploads/file.zip)</label>
<input id="dest" placeholder="auto-detect filename if empty" />
<label>Upload method</label>
<div>
<label style="display:inline;font-size:14px"><input type="radio" name="method" value="python" checked style="width:auto" /> Python (temp file, progress %, reliable)</label><br/>
<label style="display:inline;font-size:14px"><input type="radio" name="method" value="curl" style="width:auto" /> curl pipe (no disk, no progress) <small>curl -L URL | curl -u user:pass -T - DAV/file</small></label>
</div>
<div class="row">
<button id="uploadBtn">Upload</button>
</div>
<div class="bar"><div id="pbar"></div></div>
<div id="status">Idle</div>
<div class="log" id="log"></div>
</div>
<script>
const $=id=>document.getElementById(id);
for(const k of ["webdav_url","user","password","download_url","dest"]){
  const v=localStorage.getItem(k); if(v) $(k).value=v;
  $(k).addEventListener("input",e=>localStorage.setItem(k,e.target.value));
}
{const m=localStorage.getItem("method"); if(m){const r=document.querySelector('input[name=method][value="'+m+'"]'); if(r) r.checked=true;}}
function creds(){return {webdav_url:$("webdav_url").value.trim(),user:$("user").value.trim(),password:$("password").value};}
function log(m){$("log").textContent+=(m+"\\n");$("log").scrollTop=1e9;}
function fmtMB(b){return (b/1024/1024).toFixed(1)+" MB";}
$("testBtn").onclick=async()=>{
  const b=$("testBtn"); b.disabled=true; $("testStatus").textContent="Testing...";
  try{
    const r=await fetch("/api/test",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(creds())});
    const j=await r.json();
    $("testStatus").innerHTML=j.ok?'<span class="ok">✅ '+j.message+'</span>':'<span class="err">❌ '+j.message+'</span>';
  }catch(e){$("testStatus").innerHTML='<span class="err">❌ '+e+'</span>';}
  b.disabled=false;
};
let timer=null;
$("uploadBtn").onclick=async()=>{
  const b=$("uploadBtn"); b.disabled=true; $("pbar").style.width="0%";
  const m=(document.querySelector('input[name=method]:checked')||{}).value||"python";
  localStorage.setItem("method",m);
  const body={...creds(),download_url:$("download_url").value.trim(),dest:$("dest").value.trim(),method:m};
  if(!body.download_url){$("status").textContent="Enter a download link";b.disabled=false;return;}
  $("status").textContent="Starting..."; $("log").textContent="";
  let job_id=null;
  try{
    const r=await fetch("/api/upload",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j=await r.json();
    if(!j.ok){$("status").innerHTML='<span class="err">❌ '+j.message+'</span>';b.disabled=false;return;}
    job_id=j.job_id;
    log("Job "+job_id+" started");
  }catch(e){$("status").innerHTML='<span class="err">❌ '+e+'</span>';b.disabled=false;return;}
  let lastB=0,lastT=Date.now();
  timer=setInterval(async()=>{
    try{
      const r=await fetch("/api/progress?job_id="+encodeURIComponent(job_id));
      const j=await r.json();
      const pct=j.percent||0;
      $("pbar").style.width=pct+"%";
      const now=Date.now(); const dt=(now-lastT)/1000; const db=j.done_bytes-lastB;
      const spd=dt>0?db/1024/1024/dt:0; lastB=j.done_bytes; lastT=now;
      let txt=j.stage+": "+pct+"%";
      if(j.total) txt+=" ("+fmtMB(j.done_bytes)+"/"+fmtMB(j.total)+")";
      else txt+=" ("+fmtMB(j.done_bytes)+")";
      txt+=" - "+spd.toFixed(2)+" MB/s ("+(spd*8).toFixed(1)+" Mbps)";
      txt+="\\n"+j.message;
      $("status").textContent=txt;
      if(j.status==="done"){clearInterval(timer);$("pbar").style.width="100%";$("status").innerHTML='<span class="ok">✅ '+j.message+'</span>';log("DONE");b.disabled=false;}
      if(j.status==="error"){clearInterval(timer);$("status").innerHTML='<span class="err">❌ '+j.message+'</span>';log("ERROR: "+j.message);b.disabled=false;}
    }catch(e){log("poll error "+e);}
  },800);
};
</script>
</body>
</html>
"""


def guess_filename(url, resp=None):
    if resp is not None:
        cd = resp.headers.get("Content-Disposition", "")
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
    path = urllib.parse.urlparse(url).path.rstrip("/")
    name = os.path.basename(path) if path else ""
    name = urllib.parse.unquote(name)
    if name and ("." in name or len(name) > 1):
        return name
    return "downloaded_file"


def ensure_remote_dir(webdav_base, remote_dir, auth):
    if not remote_dir.strip("/ "):
        return
    parts = [p for p in remote_dir.strip("/").split("/") if p]
    cur = ""
    for part in parts:
        cur = f"{cur}/{part}" if cur else part
        url = webdav_base + "/".join(urllib.parse.quote(p, safe="") for p in cur.split("/")) + "/"
        try:
            requests.request("MKCOL", url, auth=auth, timeout=30)
        except Exception:
            pass


def test_connection(webdav_url, user, password):
    base = webdav_url.strip()
    if not base.endswith("/"):
        base += "/"
    auth = HTTPBasicAuth(user, password)
    r = requests.request("PROPFIND", base, auth=auth, headers={"Depth": "0"}, timeout=30)
    if r.status_code in (401, 403):
        return False, "Auth failed (401/403). Check Connection ID / Apps Password and enable Apps Connection in My Page."
    if r.status_code in (200, 207, 301, 302):
        return True, f"Connected! (PROPFIND {r.status_code})"
    return False, f"Unexpected WebDAV status {r.status_code}: {r.text[:300]}"


def update_job(job_id, **kw):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kw)


def _valid_http_url(u):
    try:
        p = urllib.parse.urlparse(u.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def run_job_curl(job_id, webdav_url, user, password, download_url, dest):
    """Zero-disk pipe: curl -L download_url | curl -u user:pass -T - put_url."""
    try:
        if shutil.which("curl") is None:
            raise RuntimeError("curl binary not found on server. Install curl first.")
        if not _valid_http_url(download_url) or not _valid_http_url(webdav_url):
            raise RuntimeError("Invalid URL: must start with http:// or https://")
        base = webdav_url.strip()
        if not base.endswith("/"):
            base += "/"
        download_url = download_url.strip()
        dest = (dest or "").strip().lstrip("/")

        # Guess filename for PUT target (HEAD attempt for Content-Disposition/size, else URL path)
        filename = dest
        total = 0
        if not filename:
            try:
                h = requests.head(download_url, allow_redirects=True, timeout=30,
                                  headers={"User-Agent": "Mozilla/5.0"})
                cd = h.headers.get("Content-Disposition", "")
                total = int(h.headers.get("Content-Length", 0)) or 0
                if cd:
                    m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", cd, re.I)
                    if m:
                        filename = urllib.parse.unquote(m.group(1))
                    else:
                        m = re.search(r'filename\s*=\s*"([^"]+)"', cd)
                        if m:
                            filename = m.group(1)
                if not filename:
                    filename = guess_filename(h.url or download_url, h)
            except Exception:
                filename = guess_filename(download_url)
        filename = (filename or "downloaded_file").lstrip("/")
        update_job(job_id, filename=filename, total=total, stage="curl-pipe",
                   percent=10, done_bytes=0,
                   message=f"Piping (no disk): {download_url[:120]} -> {filename}")

        auth = HTTPBasicAuth(user, password)
        parent = "/".join(filename.split("/")[:-1])
        if parent:
            ensure_remote_dir(base, parent, auth)
        put_url = base + "/".join(urllib.parse.quote(p, safe="") for p in filename.split("/"))
        print(f"[job {job_id}] curl pipe: {download_url[:150]} -> {put_url}", flush=True)

        # curl download -> stdout | curl upload stdin. No shell=True (safe args list).
        dl = subprocess.Popen(
            ["curl", "-L", "--fail", "-sS", "--connect-timeout", "30", "--max-time", "0",
             "-A", "Mozilla/5.0", download_url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        up_args = ["curl", "-sS", "--fail-with-body", "--connect-timeout", "30",
                     "-u", f"{user}:{password}",
                     "-H", "Content-Type: application/octet-stream",
                     "-T", "-", put_url]
        if total:
            # pipe size unknown to curl -> would use chunked (InfiniCLOUD rejects).
            # HEAD gave us a size, so send explicit Content-Length.
            up_args[up_args.index("-T") + 2:up_args.index("-T") + 2] = ["-H", f"Content-Length: {total}"]
        up = subprocess.Popen(up_args, stdin=dl.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Allow dl SIGPIPE if up exits early
        if dl.stdout:
            dl.stdout.close()
        _, up_out = up.communicate()
        dl_err = b""
        try:
            _, dl_err = dl.communicate(timeout=5)
        except Exception:
            try:
                dl.kill()
            except Exception:
                pass
        if up.returncode == 0 and dl.returncode in (0, None, -13):  # -13 = SIGPIPE after up done
            update_job(job_id, status="done", stage="done", percent=100,
                       done_bytes=total, message=f"Uploaded {filename} via curl pipe (no temp file).")
        else:
            detail = (up_out or b"").decode(errors="replace")[:400]
            d_err = (dl_err or b"").decode(errors="replace")[:400]
            if up.returncode == 22:
                # HTTP error from WebDAV side (auth/quota/path in body)
                tail = put_url.split("/dav/")[-1][:120] if "/dav/" in put_url else put_url[-120:]
                if "404" in detail:
                    raise RuntimeError(f"curl upload 404: WebDAV path not found for '{tail}'. Fix: 1) WebDAV URL must be https://xxx.infini-cloud.net/dav/ (with /dav/) 2) Save-as parent folder must exist (auto-created, check Test Connection first) 3) try Save-as with plain filename only, no subfolders. Detail: {detail[:250]}")
                raise RuntimeError(f"curl upload failed (HTTP error, code 22) for '{tail}'. Check user/pass, /dav/ URL, quota. Detail: {detail[:300]}")
            raise RuntimeError(f"curl pipe failed (dl={dl.returncode} up={up.returncode}). dl_err: {d_err[:200]} up: {detail[:200]}")
    except Exception as e:
        update_job(job_id, status="error", stage="error", message=str(e)[:1000])


def run_job(job_id, webdav_url, user, password, download_url, dest):
    tmp_path = None
    try:
        base = webdav_url.strip()
        if not base.endswith("/"):
            base += "/"
        auth = HTTPBasicAuth(user, password)
        download_url = download_url.strip()
        dest = (dest or "").strip().lstrip("/")

        update_job(job_id, stage="downloading", message="Starting download...", done_bytes=0, total=0, percent=0)

        # Phase 1: download to temp file (so we know exact size -> Content-Length)
        with requests.get(download_url, stream=True, timeout=60,
                          headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0)) or 0
            filename = dest or guess_filename(download_url, r)
            filename = filename.lstrip("/")
            update_job(job_id, filename=filename, total=total,
                       message=f"Downloading {filename} ({total/1024/1024:.1f} MB)" if total else f"Downloading {filename} (size unknown)")

            fd, tmp_path = tempfile.mkstemp(prefix="infinicloud_")
            downloaded = 0
            try:
                with os.fdopen(fd, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            pct = int(downloaded * 100 / total) // 2 if total else 0  # download = 0-50%
                            update_job(job_id, done_bytes=downloaded, total=total, percent=pct,
                                       message=f"Downloading {filename}: {downloaded/1024/1024:.1f} MB" + (f" / {total/1024/1024:.1f} MB" if total else ""))
            except Exception as e:
                raise RuntimeError(f"Download failed after {downloaded} bytes: {e}")

        size = os.path.getsize(tmp_path)
        update_job(job_id, stage="uploading", total=size, done_bytes=0, percent=50,
                   message=f"Download complete ({size/1024/1024:.1f} MB). Uploading to InfiniCLOUD...")

        # Phase 2: upload with known Content-Length + progress
        parent = "/".join(filename.split("/")[:-1])
        if parent:
            ensure_remote_dir(base, parent, auth)
        put_url = base + "/".join(urllib.parse.quote(p, safe="") for p in filename.split("/"))

        print(f"[job {job_id}] temp file: {tmp_path} ({size} bytes) -> {put_url}", flush=True)

        class ProgressReader:
            def __init__(self, path, size):
                self.f = open(path, "rb")
                self.size = size
                self.uploaded = 0
            def read(self, n=-1):
                chunk = self.f.read(n if n > 0 else 1024 * 1024)
                if chunk:
                    self.uploaded += len(chunk)
                    pct = 50 + int(self.uploaded * 50 / self.size) if self.size else 50
                    update_job(job_id, done_bytes=self.uploaded, percent=pct,
                               message=f"Uploading {filename}: {self.uploaded/1024/1024:.1f} / {self.size/1024/1024:.1f} MB | tmp: {tmp_path}")
                return chunk
            def __len__(self):
                return self.size
            def close(self):
                try:
                    self.f.close()
                except Exception:
                    pass

        reader = ProgressReader(tmp_path, size)
        try:
            # file-like + explicit Content-Length + empty Expect avoids chunked encoding
            # which InfiniCLOUD Apache mod_dav rejects with BrokenPipe.
            put = requests.put(put_url, data=reader, auth=auth,
                               headers={"Content-Length": str(size),
                                        "Content-Type": "application/octet-stream",
                                        "Expect": ""},
                               timeout=(30, 300))
        except BrokenPipeError as e:
            raise RuntimeError(f"Upload connection broken (BrokenPipe). Server closed connection. Check: 1) WebDAV URL ends with /dav/ 2) user/pass correct 3) enough quota 4) filename valid. Detail: {e}")
        except requests.exceptions.ChunkedEncodingError as e:
            raise RuntimeError(f"Upload interrupted (server closed connection during PUT of {size} bytes to {put_url.split('/dav/')[-1][:100]}). Likely: wrong WebDAV URL, auth/quota issue, or file too large. Test with a 1KB file first. Detail: {e}")
        except requests.exceptions.ConnectionError as e:
            # requests wraps BrokenPipe here
            msg = str(e)
            if "Broken pipe" in msg or "Connection broken" in msg:
                raise RuntimeError(f"Upload connection broken during PUT of {size} bytes. Common causes: wrong WebDAV URL (must be https://xxx.infini-cloud.net/dav/), auth failed, quota full, or file too large. Try 1KB test file. Detail: {msg[:300]}")
            raise RuntimeError(f"Upload connection error: {msg[:300]}")
        finally:
            try:
                reader.close()
            except Exception:
                pass

        print(f"[job {job_id}] PUT status {put.status_code}", flush=True)
        if put.status_code in (200, 201, 204):
            update_job(job_id, status="done", stage="done", percent=100, done_bytes=size,
                       message=f"Uploaded {filename} ({size/1024/1024:.2f} MB) to InfiniCLOUD.")
        elif put.status_code in (401, 403):
            raise RuntimeError(f"Upload auth failed ({put.status_code}). Check Connection ID / Apps Password.")
        elif put.status_code == 507:
            raise RuntimeError("Upload failed 507: quota exceeded (not enough space on InfiniCLOUD).")
        else:
            raise RuntimeError(f"Upload failed {put.status_code}: {put.text[:500]}")
    except Exception as e:
        update_job(job_id, status="error", stage="error", message=str(e)[:1000])
    finally:
        keep = os.environ.get("KEEP_TMP", "0") == "1"
        if tmp_path and os.path.exists(tmp_path):
            if keep:
                print(f"[job {job_id}] KEEP_TMP=1, keeping {tmp_path}", flush=True)
            else:
                try:
                    os.remove(tmp_path)
                    print(f"[job {job_id}] cleaned tmp {tmp_path}", flush=True)
                except Exception:
                    pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            data = PAGE_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/progress":
            qs = urllib.parse.parse_qs(parsed.query)
            job_id = (qs.get("job_id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                return self.send_json({"status": "error", "stage": "error", "percent": 0, "done_bytes": 0, "total": 0, "message": "Unknown job_id"}, 404)
            return self.send_json(job)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self.send_json({"ok": False, "message": "Invalid JSON"}, 400)

        if self.path == "/api/test":
            if not all([body.get("webdav_url"), body.get("user"), body.get("password")]):
                return self.send_json({"ok": False, "message": "Fill WEBDAV_URL, USER, PASS first."}, 400)
            try:
                ok, msg = test_connection(body["webdav_url"], body["user"], body["password"])
                return self.send_json({"ok": ok, "message": msg})
            except Exception as e:
                return self.send_json({"ok": False, "message": f"Connection error: {e}"})

        if self.path == "/api/upload":
            for k in ("webdav_url", "user", "password", "download_url"):
                if not body.get(k):
                    return self.send_json({"ok": False, "message": f"Missing field: {k}"}, 400)
            method = (body.get("method") or "python").strip().lower()
            if method not in ("python", "curl"):
                method = "python"
            job_id = uuid.uuid4().hex[:12]
            with JOBS_LOCK:
                JOBS[job_id] = {"status": "running", "stage": "queued", "percent": 0,
                                "done_bytes": 0, "total": 0, "filename": "",
                                "message": f"Queued ({method})..."}
            target = run_job_curl if method == "curl" else run_job
            t = threading.Thread(target=target, args=(job_id, body["webdav_url"], body["user"],
                                                      body["password"], body["download_url"], body.get("dest", "")),
                                 daemon=True)
            t.start()
            return self.send_json({"ok": True, "job_id": job_id, "message": "Started", "method": method})

        return self.send_json({"ok": False, "message": "Unknown endpoint"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Open http://{a.host}:{a.port}", flush=True)
    print(f"Temp dir: {tempfile.gettempdir()} (set KEEP_TMP=1 to keep files for debug)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
