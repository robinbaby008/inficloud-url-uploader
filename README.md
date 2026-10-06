# inficloud-url-uploader

Transfer files to InfiniCLOUD by giving a direct download link.
InfiniCLOUD has no server-side "fetch URL" feature, so this app runs on
your machine/VPS and proxies: `download link -> this server -> InfiniCLOUD WebDAV`.

## Files

- `app.py` - web UI + backend (recommended)
- `url_to_infinicloud.py` - CLI version

## Requirements

- Python 3 + `requests`
- `curl` binary (only for curl-pipe mode)

## 1. Get InfiniCLOUD credentials

InfiniCLOUD My Page > Apps Connection > Turn ON:

- WebDAV URL, e.g. `https://xxxx.infini-cloud.net/dav/`
- Connection ID
- Apps Password (shown once, Reissue if lost)

## 2. Run the server

```bash
cd /home/robin/Android/inficloud
python3 -u app.py --port 8000
# output:
# Open http://127.0.0.1:8000
# Temp dir: /tmp (...)
```

Keep that terminal open, then open in a browser on the **same machine**:

- `http://localhost:8000` or `http://127.0.0.1:8000`

Other port:

```bash
python3 -u app.py --port 8901
```

Allow LAN / other device:

```bash
python3 -u app.py --host 0.0.0.0 --port 8000
# open http://YOUR-PC-IP:8000
```

## 3. Use it

1. Fill `INFINICLOUD_WEBDAV_URL`, `INFINICLOUD_USER`, `INFINICLOUD_PASS`
2. Click `Test Connection` (must be green first)
3. Enter direct download link + optional `Save as` (e.g. `uploads/file.zip`)
4. Pick method, click `Upload`

### Methods

- **Python (temp file)**: download to `/tmp/infinicloud_*`, then PUT with
  `Content-Length`. Shows download 0-50% + upload 50-100% with MB/s.
  Most reliable. Needs disk space.
- **curl pipe**: `curl -L URL | curl -u user:pass -T - DAV/file`.
  Zero disk, no progress % (pipe size unknown). Needs `curl` installed.

### CLI

```bash
export INFINICLOUD_WEBDAV_URL="https://xxxx.infini-cloud.net/dav/"
export INFINICLOUD_USER="your-id"
export INFINICLOUD_PASS="apps-password"

python3 url_to_infinicloud.py "https://example.com/file.zip"
python3 url_to_infinicloud.py "https://example.com/file.zip" --dest "backup/file.zip"
```

## Troubleshooting

- Page won't open: server not running, wrong port, or wrong machine.
  Keep `app.py` running, hard-refresh (`Ctrl+Shift+R`).
- `Test Connection` red 401/403: wrong ID/pass or Apps Connection off.
- curl 404: WebDAV URL must end with `/dav/`, try plain `file.mp4`
  as Save-as (no subfolders) first.
- BrokenPipe during PUT: wrong URL, auth, quota full (507), or file
  too large. Try 1KB file first.
- Temp files: exist only during transfer in `tempfile.gettempdir()`
  (`python3 -c "import tempfile; print(tempfile.gettempdir())"`).
  Auto-deleted after. Set `KEEP_TMP=1` to keep for debug:
  `KEEP_TMP=1 python3 -u app.py --port 8000`
- Speed ~1 Mbps: data flows through your machine. Slow phase
  (download vs upload) shows in UI. For speed, run on a fast VPS.

## Notes

- Link must be a direct download link (not preview pages).
- Credentials stay on your machine (browser localStorage + localhost).
