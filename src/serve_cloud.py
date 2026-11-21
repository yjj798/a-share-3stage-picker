# -*- coding: utf-8 -*-
"""serve_cloud.py · 腾讯云静态站服务（替换原 zt-quant 888 端口）

- 纯标准库，Python 3.9+（云端 3.14 实测）
- 服务 out/ 目录下的自包含 HTML/CSV
- 根路径 / 302 到 股票池.html
- 访问口令与旧 zt-quant 同习惯：?token=xxx 首访，Cookie 记 7 天
  token 从环境变量 STOCKPOOL_TOKEN 读（systemd 注入，不落代码）
- 页面每日重生成同名覆盖 ⇒ 一律 Cache-Control: no-cache，防浏览器拿旧页

用法：
    STOCKPOOL_TOKEN=xxx python serve_cloud.py --host 0.0.0.0 --port 888
"""
import argparse
import http.cookiejar  # noqa: F401 (文档用)
import json
import threading
import mimetypes
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
ROOT_PAGE = "简版股票池.html"   # 2026-11-21 human 令：精简系统为首页，旧股票池.html 不再默认
COOKIE_NAME = "sp_token"
COOKIE_DAYS = 7
TOKEN = os.environ.get("STOCKPOOL_TOKEN", "")
NO_TOKEN_OK = os.environ.get("STOCKPOOL_NO_TOKEN") == "1"

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


def _read_token_cookie(header: str) -> str:
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME:
            return urllib.parse.unquote(v)
    return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "stockpool/1.0"
    protocol_version = "HTTP/1.1"
    _refresh_lock = threading.Lock()
    _last_refresh = [0.0]

    # ---------- 认证 ----------
    def _authorized(self, qs):
        if NO_TOKEN_OK:
            return True, None
        supplied = ""
        if "token" in qs:
            supplied = qs["token"][0]
        if TOKEN and supplied == TOKEN:
            return True, "set"          # 本请求带对 token ⇒ 顺带种 Cookie
        if TOKEN and _read_token_cookie(self.headers.get("Cookie")) == TOKEN:
            return True, None
        return False, supplied

    def _deny(self):
        body = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>401</title></head>"
            "<body style='font-family:sans-serif;padding:40px'>"
            "<h1>401 未授权</h1><p>请在地址后加 <code>?token=你的口令</code>。</p></body></html>"
        ).encode("utf-8")
        self._send(401, "text/html; charset=utf-8", body)

    # ---------- 响应 ----------
    def _send(self, code, ctype, body, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_gz(self, code, ctype, body, raw_len, extra=()):
        """返回 gzip 原始字节（不声明 content-encoding，由 Worker 补）。

        2026-11-11：页面 1.7MB 经快速隧道 + Worker 整块缓冲，实测 40s 都传不完 ⇒
        浏览器白屏。改由源站回预压字节（1.7MB→220KB），Worker 只贴 content-encoding。
        """
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Encoding", "gzip")   # 由源站自己声明，Worker 原样透传
        self.send_header("x-sp-gz-ok", "1")
        self.send_header("x-sp-raw-size", str(raw_len))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, loc, extra=()):
        self.send_response(302)
        self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()

    def _notfound(self, path):
        body = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>404</title></head>"
            "<body style='font-family:sans-serif;padding:40px'>"
            "<h1>404</h1><p>本站是静态股池站，没有 <code>%s</code> 这个文件。</p>"
            "<p><a href='/'>回股票池首页</a></p></body></html>" % path
        ).encode("utf-8")
        self._send(404, "text/html; charset=utf-8", body)

    # ---------- 请求处理 ----------
    def _maybe_refresh(self, fp):
        """简版页被请求时，若超过2小时未刷新才同步重算(安全节流，避免封IP)。
        文件锁跨进程可靠；成功刷新后写时间戳，重启服务也不会立刻重复抓。"""
        import time
        import subprocess
        REFRESH_SEC = 7200   # 安全间隔2小时（原1小时太快，663次POST易被风控）
        stamp = os.path.join(OUT, "_last_refresh.txt")
        now = time.time()
        # 双保险：内存间隔 + 文件时间戳（跨进程/重启）
        if now - Handler._last_refresh[0] < REFRESH_SEC:
            return
        try:
            if os.path.isfile(stamp) and now - os.path.getmtime(stamp) < REFRESH_SEC:
                return
        except OSError:
            pass
        try:
            age = now - os.path.getmtime(fp)
            if age < REFRESH_SEC:
                return   # 页面还新鲜，不刷新
        except OSError:
            pass
        if not Handler._refresh_lock.acquire(blocking=False):
            # 别人正在刷新，等它完成(最多20秒)
            for _ in range(40):
                if now - os.path.getmtime(fp) < 60:
                    break
                time.sleep(0.5)
                now = time.time()
            return
        Handler._last_refresh[0] = now
        try:
            py = sys.executable
            appdir = os.path.join(HERE, "app")
            refresh_all = os.path.join(appdir, "refresh_all.py")
            if os.path.isfile(refresh_all):
                subprocess.run([py, refresh_all, '--force'], cwd=appdir,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
                sys.stderr.write("[refresh] done: refresh_all (throttled 2h)\n")
            # 时间戳由 refresh_all 内部写，这里补写防重复
            with open(stamp, 'w') as _f:
                _f.write(time.strftime('%Y-%m-%d %H:%M:%S'))
        except Exception as e:
            sys.stderr.write("[refresh] failed: %s\n" % e)
        finally:
            Handler._refresh_lock.release()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        ok, mode = self._authorized(qs)
        if not ok:
            self._deny()
            return
        cookie_hdr = []
        if mode == "set":
            import time
            exp = time.strftime(
                "%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + COOKIE_DAYS * 86400))
            cookie_hdr.append(("Set-Cookie",
                               "%s=%s; Path=/; Max-Age=%d; Expires=%s"
                               % (COOKIE_NAME, TOKEN, COOKIE_DAYS * 86400, exp)))

        path = urllib.parse.unquote(parsed.path)
        if path in ("", "/"):
            self._redirect("/" + urllib.parse.quote(ROOT_PAGE), cookie_hdr)
            return
        # 归一化：拒绝跳出 out/ 的路径
        rel = os.path.normpath(path.lstrip("/"))
        if rel.startswith("..") or os.path.isabs(rel):
            self._notfound(path)
            return
        fp = os.path.join(OUT, rel)
        if not os.path.isfile(fp):
            self._notfound(path)
            return

        # 简版页面：热度超过1小时未更新则后台异步重算（热度+评分跟着刷新）
        if rel == ROOT_PAGE and os.environ.get("SP_AUTO_REFRESH", "1") == "1":
            self._maybe_refresh(fp)

        ext = os.path.splitext(fp)[1].lower()
        ctype = TYPES.get(ext) or (mimetypes.guess_type(fp)[0] or "application/octet-stream")
        # Worker 回源时带 x-sp-gz: 1 ⇒ 优先回预压的 .gz 字节（体积 1/5~1/8）
        gf = fp + ".gz"
        try:
            fresh = os.path.isfile(gf) and os.path.getmtime(gf) + 2 >= os.path.getmtime(fp)
        except OSError:
            fresh = False
        if self.headers.get("x-sp-gz") == "1" and fresh:
            with open(gf, "rb") as f:
                gz = f.read()
            self._send_gz(200, ctype, gz, os.path.getsize(fp), cookie_hdr)
            return
        with open(fp, "rb") as f:
            body = f.read()
        self._send(200, ctype, body, cookie_hdr)

    do_HEAD = do_GET

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    ap = argparse.ArgumentParser(description="stockpool 云端静态服务")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", default="888",
                    help="端口，可逗号分隔多端口：888,8080（8080 给 CF Worker 回源，888 给直连 IP）")
    a = ap.parse_args()
    if not os.path.isdir(OUT):
        sys.exit("out/ 目录不存在: %s" % OUT)
    if not TOKEN and not NO_TOKEN_OK:
        print("[warn] STOCKPOOL_TOKEN 未设置，未带 token 将一律 401", file=sys.stderr)
    import threading
    servers = []
    for p in [int(x) for x in str(a.port).split(",") if x.strip()]:
        httpd = ThreadingHTTPServer((a.host, p), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append((p, httpd))
        print("[serve] %s -> %s (root /%s) port %d" % (HERE, OUT, ROOT_PAGE, p), flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
