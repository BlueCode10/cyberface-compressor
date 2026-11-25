"""Cyberface Compressor: make an NBA 2K14 cyberface (png####.iff) smaller (see compressor.py for how).

A local-only HTTP server (127.0.0.1, random port, per-launch token) and index.html in a Microsoft Edge app window,
like the Cyberface Viewer. Open a file with the Open button, by dropping it on the window, or by dropping it on
CyberfaceCompressor.exe (first command-line argument). The original file is never changed: Compress asks where
to save the smaller copy (default: a Compressed folder next to the original, same file name).
"""
import os, sys, json, time, secrets, threading, subprocess, ctypes, traceback, urllib.parse
from ctypes import wintypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

VERSION = '1.0.0'
FROZEN = getattr(sys, 'frozen', False)
BASE = sys._MEIPASS if FROZEN else os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(BASE, 'index.html')
DATA = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'CyberfaceCompressor')
sys.path.insert(0, BASE)
import compressor  # noqa: E402

TOKEN = secrets.token_urlsafe(24)
TEMP = os.path.join(DATA, 'drop'); os.makedirs(TEMP, exist_ok=True)
LOCK = threading.Lock()
STATE = dict(path=None, name=None, info=None, busy=False, log=[], result=None, error=None, stamp=0,
             last_ping=time.time())


# ------------------------------------------------------------------ native dialogs
class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [('lStructSize', wintypes.DWORD), ('hwndOwner', wintypes.HWND), ('hInstance', wintypes.HINSTANCE),
                ('lpstrFilter', wintypes.LPCWSTR), ('lpstrCustomFilter', wintypes.LPWSTR), ('nMaxCustFilter', wintypes.DWORD),
                ('nFilterIndex', wintypes.DWORD), ('lpstrFile', wintypes.LPWSTR), ('nMaxFile', wintypes.DWORD),
                ('lpstrFileTitle', wintypes.LPWSTR), ('nMaxFileTitle', wintypes.DWORD), ('lpstrInitialDir', wintypes.LPCWSTR),
                ('lpstrTitle', wintypes.LPCWSTR), ('Flags', wintypes.DWORD), ('nFileOffset', wintypes.WORD),
                ('nFileExtension', wintypes.WORD), ('lpstrDefExt', wintypes.LPCWSTR), ('lCustData', wintypes.LPARAM),
                ('lpfnHook', ctypes.c_void_p), ('lpTemplateName', wintypes.LPCWSTR), ('pvReserved', ctypes.c_void_p),
                ('dwReserved', wintypes.DWORD), ('FlagsEx', wintypes.DWORD)]


def _dialog(save, initial, default_name, title):
    buf = ctypes.create_unicode_buffer(default_name or '', 4096)
    ofn = OPENFILENAMEW(); ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
    ofn.lpstrFilter = 'NBA 2K14 cyberface (*.iff)\0*.iff\0\0'
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR); ofn.nMaxFile = 4096
    ofn.lpstrTitle = title; ofn.lpstrInitialDir = initial; ofn.lpstrDefExt = 'iff'
    if save:
        ofn.Flags = 0x80000 | 0x8 | 0x800 | 0x2          # explorer, no chdir, path must exist, confirm overwrite
        ok = ctypes.windll.comdlg32.GetSaveFileNameW(ctypes.byref(ofn))
    else:
        ofn.Flags = 0x80000 | 0x8 | 0x800 | 0x1000
        ok = ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn))
    return buf.value if ok else None


def _src_dir():
    p = STATE['path']
    return os.path.dirname(p) if p and not p.startswith(TEMP) else None


# ------------------------------------------------------------------ jobs
def open_path(path, name=None):
    try:
        info = compressor.inspect(path); err = None
    except Exception as e:
        info = None
        err = str(e) if isinstance(e, ValueError) else 'Could not read %s: %s' % (os.path.basename(path), e)
    with LOCK:
        if err:
            STATE['error'] = err
        else:
            STATE.update(path=path, name=name or os.path.basename(path), info=info, log=[], result=None, error=None)
        STATE['stamp'] += 1


def _log(msg):
    with LOCK:
        STATE['log'].append(msg); STATE['stamp'] += 1


def _compress_job(src, dst, sizes):
    try:
        r = compressor.compress(src, dst, sizes, log=_log); r['path'] = dst; err = None
    except Exception as e:
        r = None; err = 'Compression failed: %s' % e
    with LOCK:
        STATE['busy'] = False; STATE['result'] = r
        if err:
            STATE['error'] = err
        STATE['stamp'] += 1


# ------------------------------------------------------------------ server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data))); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(data)

    def _auth(self, q):
        return self.headers.get('X-Token') == TOKEN or q.get('t', [''])[0] == TOKEN

    def do_GET(self):
        url = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(url.query)
        if self.headers.get('Host', '').split(':')[0] not in ('127.0.0.1', 'localhost'):
            return self._send(403, {'error': 'forbidden'})
        if url.path in ('/', '/index.html'):
            html = open(PAGE, encoding='utf-8').read().replace('__TOKEN__', TOKEN).replace('__VERSION__', VERSION)
            return self._send(200, html.encode(), 'text/html; charset=utf-8')
        if url.path == '/favicon.ico':        # window / taskbar icon of the Edge app window
            p = os.path.join(BASE, 'icon.ico')
            return self._send(200, open(p, 'rb').read(), 'image/x-icon') if os.path.isfile(p) else self._send(404, b'')
        if not self._auth(q):
            return self._send(403, {'error': 'forbidden'})
        if url.path == '/api/state':
            STATE['last_ping'] = time.time()
            with LOCK:
                s = {k: STATE[k] for k in ('name', 'path', 'info', 'busy', 'log', 'result', 'error', 'stamp')}
                s['dropped'] = bool(STATE['path'] and STATE['path'].startswith(TEMP))
                return self._send(200, s)
        return self._send(404, {'error': 'not found'})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(url.query)
        if self.headers.get('Host', '').split(':')[0] not in ('127.0.0.1', 'localhost') or not self._auth(q):
            return self._send(403, {'error': 'forbidden'})
        n = int(self.headers.get('Content-Length') or 0)
        if url.path == '/api/open':
            self.rfile.read(n)
            with LOCK:
                busy, initial = STATE['busy'], _src_dir()
            if busy:
                return self._send(409, {'error': 'busy compressing'})
            p = _dialog(False, initial, None, 'Open an NBA 2K14 cyberface')
            if p:
                open_path(p)
            return self._send(200, dict(ok=True))
        if url.path == '/api/compress':
            body = json.loads(self.rfile.read(n) or b'{}')
            sizes = {k: int(v) for k, v in (body.get('sizes') or {}).items() if str(v).isdigit()}
            with LOCK:
                src, name, busy, initial = STATE['path'], STATE['name'], STATE['busy'], _src_dir()
            if not src:
                return self._send(409, {'error': 'open a file first'})
            if busy:
                return self._send(409, {'error': 'already compressing'})
            out_dir = os.path.join(initial, 'Compressed') if initial else None
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            dst = _dialog(True, out_dir, name, 'Save the compressed cyberface')
            if not dst:
                if out_dir and not os.listdir(out_dir):
                    os.rmdir(out_dir)
                return self._send(200, dict(ok=False))
            if os.path.abspath(dst) == os.path.abspath(src):
                return self._send(409, {'error': 'pick another location: the original is never overwritten'})
            with LOCK:
                STATE.update(busy=True, log=[], result=None, error=None); STATE['stamp'] += 1
            threading.Thread(target=_compress_job, args=(src, dst, sizes), daemon=True).start()
            return self._send(200, dict(ok=True))
        if url.path == '/api/reveal':
            self.rfile.read(n)
            with LOCK:
                r = STATE['result']
            if r and os.path.isfile(r['path']):
                subprocess.Popen(['explorer', '/select,', os.path.normpath(r['path'])])
            return self._send(200, dict(ok=True))
        if url.path == '/api/drop':
            if n > 200 * 1024 * 1024:
                return self._send(413, {'error': 'file too large'})
            data = self.rfile.read(n)
            with LOCK:
                if STATE['busy']:
                    return self._send(409, {'error': 'busy compressing'})
            name = os.path.basename(q.get('name', ['dropped.iff'])[0]) or 'dropped.iff'
            for old in os.listdir(TEMP):
                try:
                    os.remove(os.path.join(TEMP, old))
                except OSError:
                    pass
            p = os.path.join(TEMP, name)
            with open(p, 'wb') as fh:
                fh.write(data)
            open_path(p, name)
            return self._send(200, dict(ok=True))
        return self._send(404, {'error': 'not found'})


def find_edge():
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        if base:
            p = os.path.join(base, 'Microsoft', 'Edge', 'Application', 'msedge.exe')
            if os.path.exists(p):
                return p
    return None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    if args and os.path.isfile(args[0]):
        open_path(os.path.abspath(args[0]))
    dev_port = int(os.environ.get('CFC_DEV_PORT') or 0)    # development: serve only, no window
    srv = ThreadingHTTPServer(('127.0.0.1', dev_port), Handler)
    if dev_port:
        print('serving http://127.0.0.1:%d/?t=%s' % (dev_port, TOKEN), flush=True); srv.serve_forever(); return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:%d/' % srv.server_address[1]
    edge = find_edge(); started = time.time(); proc = None
    if edge:
        proc = subprocess.Popen([edge, '--app=' + url, '--user-data-dir=' + os.path.join(DATA, 'window'),
                                 '--window-size=760,640', '--no-first-run', '--no-default-browser-check',
                                 '--disable-features=Translate,msEdgeSidebarV2'])
    else:
        import webbrowser; webbrowser.open(url)
    if proc:
        proc.wait()
    if not proc or time.time() - started < 8:    # Edge handed the window to another process: wait for the page
        while time.time() - STATE['last_ping'] < 60 or STATE['busy']:
            time.sleep(5)
    while STATE['busy']:                          # window closed mid-job: let the file finish writing
        time.sleep(1)
    srv.shutdown()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, 'crash.log'), 'w', encoding='utf-8') as fh:
            fh.write(traceback.format_exc())
