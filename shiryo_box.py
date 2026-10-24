#!/usr/bin/env python3
"""資料箱 (Shiryo Box) - ローカルで動く資料画像管理アプリ

使い方:
    資料箱.exe / python shiryo_box.py   # 隣の library フォルダを使う
    python shiryo_box.py D:/資料         # 好きなフォルダを使う
    python shiryo_box.py --browser       # アプリ窓ではなくブラウザで開く

Python 3.9 以上。pywebview があれば専用ウィンドウ、無ければブラウザで開きます。
Pillow があれば TIFF / PSD / CMYK 画像も表示できます。
"""
import io
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from PIL import Image, ImageCms
    Image.MAX_IMAGE_PIXELS = 400_000_000  # 印刷用の大きな TIFF でも警告で止めない
except ImportError:
    Image = ImageCms = None

PORT = next((int(a[7:]) for a in sys.argv[1:] if a.startswith("--port=")), 8765)
WEB_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"}
CONVERT_EXT = {".tif", ".tiff", ".psd"}  # ブラウザで表示できないので変換して見せる
IMG_EXT = WEB_EXT | (CONVERT_EXT if Image else set())
MAX_BYTES = 200 * 1024 * 1024
VIEW_MAX = 4096  # 変換表示の長辺上限

FROZEN = getattr(sys, "frozen", False)
HERE = Path(sys.executable if FROZEN else __file__).resolve().parent
RES = Path(getattr(sys, "_MEIPASS", HERE))
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = Path(ARGS[0]).expanduser().resolve() if ARGS else HERE / "library"
IMAGES = BASE / "images"
TRASH = BASE / "trash"
CACHE = BASE / "cache"
DB_PATH = BASE / "db.json"
APP_HTML = RES / "app.html"

lock = threading.Lock()


# ---------- CMYK ----------
def find_cmyk_profile():
    """library/cmyk.icc があればそれ、無ければ Windows 標準の RSWOP.icm"""
    for p in (BASE / "cmyk.icc", BASE / "cmyk.icm",
              Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/spool/drivers/color/RSWOP.icm"):
        if p.is_file():
            return p
    return None


class ColorConv:
    def __init__(self):
        self.lock = threading.Lock()
        self.name = "簡易換算"
        self.cmyk = self.to_cmyk = self.to_rgb = None
        path = find_cmyk_profile() if ImageCms else None
        if not path:
            return
        try:
            self.cmyk = ImageCms.getOpenProfile(str(path))
            srgb = ImageCms.createProfile("sRGB")
            intent = ImageCms.Intent.RELATIVE_COLORIMETRIC
            self.to_cmyk = ImageCms.buildTransform(srgb, self.cmyk, "RGB", "CMYK", intent)
            self.to_rgb = ImageCms.buildTransform(self.cmyk, srgb, "CMYK", "RGB", intent)
            self.name = ImageCms.getProfileDescription(self.cmyk).strip() or path.name
        except Exception as e:
            print("CMYK プロファイルを読めませんでした:", e)
            self.cmyk = self.to_cmyk = self.to_rgb = None

    def rgb2cmyk(self, colors):
        """[[r,g,b] 0-255] -> [[c,m,y,k] 0-100]"""
        if not colors:
            return []
        if self.to_cmyk:
            im = Image.frombytes("RGB", (len(colors), 1), bytes(min(255, max(0, round(v))) for c in colors for v in c[:3]))
            with self.lock:
                b = ImageCms.applyTransform(im, self.to_cmyk).tobytes()
            return [[round(v / 2.55) for v in b[i:i + 4]] for i in range(0, len(b), 4)]
        res = []
        for c in colors:
            r, g, b = (v / 255 for v in c[:3])
            k = 1 - max(r, g, b)
            if k >= 1:
                res.append([0, 0, 0, 100])
                continue
            res.append([round((1 - x - k) / (1 - k) * 100) for x in (r, g, b)] + [round(k * 100)])
        return res

    def cmyk2rgb(self, colors):
        """[[c,m,y,k] 0-100] -> [[r,g,b] 0-255]"""
        if not colors:
            return []
        if self.to_rgb:
            im = Image.frombytes("CMYK", (len(colors), 1), bytes(round(min(100, max(0, v)) * 2.55) for c in colors for v in c[:4]))
            with self.lock:
                b = ImageCms.applyTransform(im, self.to_rgb).tobytes()
            return [list(b[i:i + 3]) for i in range(0, len(b), 3)]
        return [[round(255 * (1 - x / 100) * (1 - c[3] / 100)) for x in c[:3]] for c in colors]


CONV = None


def image_mode(path):
    """画像のカラーモード (CMYK / RGB / ...)。Pillow が無ければ None"""
    if not Image:
        return None
    try:
        with Image.open(path) as im:
            return im.mode
    except Exception:
        return None


def needs_convert(it):
    return Path(it["file"]).suffix.lower() in CONVERT_EXT or it.get("mode") == "CMYK"


def view_file(it):
    """ブラウザ表示用ファイル。TIFF/PSD/CMYK は sRGB に変換して cache に置く"""
    src = IMAGES / it["file"]
    if not (Image and needs_convert(it)):
        return src
    with convert_lock:
        return _convert(it, src)


convert_lock = threading.Lock()


def _convert(it, src):
    st = src.stat()
    key = f"{it['id']}_{int(st.st_mtime)}_{st.st_size}"
    hit = next(CACHE.glob(key + ".*g"), None) if CACHE.exists() else None
    if hit:
        return hit
    CACHE.mkdir(parents=True, exist_ok=True)
    drop_cache(it["id"])
    with Image.open(src) as im:  # 開いた直後が PSD は統合画像、TIFF は1ページ目
        im.load()
        if im.mode == "CMYK":
            icc = im.info.get("icc_profile")
            try:
                prof = ImageCms.ImageCmsProfile(io.BytesIO(icc)) if icc else CONV.cmyk
                im = ImageCms.profileToProfile(im, prof, ImageCms.createProfile("sRGB"), outputMode="RGB") if prof else im.convert("RGB")
            except Exception:
                im = im.convert("RGB")
        elif im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
            im = im.convert("RGBA")
        else:
            im = im.convert("RGB")
        im.thumbnail((VIEW_MAX, VIEW_MAX))
        alpha = im.mode == "RGBA"
        out = CACHE / (key + (".png" if alpha else ".jpg"))
        tmp = CACHE / (key + ".tmp")
        im.save(tmp, "PNG", compress_level=3) if alpha else im.save(tmp, "JPEG", quality=92)
    os.replace(tmp, out)
    return out


def drop_cache(iid):
    if CACHE.exists():
        for old in CACHE.glob(iid + "_*"):
            old.unlink(missing_ok=True)


def new_board():
    return {"items": [], "gray": False}


def load_db():
    if DB_PATH.exists():
        try:
            db = json.loads(DB_PATH.read_text("utf-8"))
            db.setdefault("items", {})
            db.setdefault("boards", {})
            if not db["boards"]:
                db["boards"]["メイン"] = new_board()
            return db
        except Exception as e:  # 壊れていたら退避して作り直す
            print("db.json を読めなかったので退避しました:", e)
            shutil.copy(DB_PATH, DB_PATH.with_suffix(".broken.json"))
    return {"items": {}, "boards": {"メイン": new_board()}}


def save_db():
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(DB, ensure_ascii=False), "utf-8")
    os.replace(tmp, DB_PATH)


def scan():
    """images フォルダを見て、手で入れたファイルの追加・消えたファイルの削除をする"""
    known = {it["file"] for it in DB["items"].values()}
    changed = False
    for p in IMAGES.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMG_EXT:
            rel = p.relative_to(IMAGES).as_posix()
            if rel not in known:
                add_item(rel, p.stem, p.stat().st_mtime)
                changed = True
    for iid, it in list(DB["items"].items()):
        if not (IMAGES / it["file"]).exists():
            del DB["items"][iid]
            drop_cache(iid)
            changed = True
        elif "mode" not in it and Image:  # 旧バージョンの db にはカラーモードが無い
            it["mode"] = image_mode(IMAGES / it["file"])
            changed = True
    if changed:
        save_db()


def add_item(rel, name, added=None):
    iid = uuid.uuid4().hex[:12]
    DB["items"][iid] = {
        "id": iid,
        "file": rel,
        "name": name,
        "added": added or time.time(),
        "colors": None,
        "mode": image_mode(IMAGES / rel),
    }
    return DB["items"][iid]


def unique_path(folder, filename):
    filename = "".join(c for c in os.path.basename(filename) if c not in '\\/:*?"<>|\0').strip()
    stem, ext = os.path.splitext(filename)
    stem = stem[:80] or "image"
    if ext.lower() not in IMG_EXT:
        ext = ".png"
    p = folder / f"{stem}{ext}"
    n = 2
    while p.exists():
        p = folder / f"{stem}_{n}{ext}"
        n += 1
    return p


def store_bytes(data, filename):
    p = unique_path(IMAGES, filename)
    p.write_bytes(data)
    return add_item(p.relative_to(IMAGES).as_posix(), p.stem)


# ---------- URL からの取り込み (pixiv / X / 一般ページ) ----------
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PIXIV = "https://www.pixiv.net/"
MAX_PAGES = 20  # pixiv の漫画などで一度に取り込む上限
CTYPE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
             "image/bmp": ".bmp", "image/avif": ".avif", "image/tiff": ".tif", "image/vnd.adobe.photoshop": ".psd"}
PXIMG_RE = re.compile(r"^https://i\.pximg\.net/(?:c/[^/]+/)?(?:img-master|custom-thumb|img-original)/img/"
                      r"((?:\d+/){6})(\d+)_p(\d+)")
PIXIV_WORK_RE = re.compile(r"^https://(?:www\.)?pixiv\.net/(?:[a-z]{2}/)?(?:artworks/|member_illust\.php\?.*illust_id=)(\d+)")
TWEET_RE = re.compile(r"^https://(?:(?:www|mobile)\.)?(?:twitter|x)\.com/(?:i/web|[^/]+)/status(?:es)?/(\d+)")


def http_get(url, referer=None, limit=MAX_BYTES):
    """(Content-Type, 中身, 最終URL)。403 なら同じサイトから来たことにしてもう一度試す"""
    for ref in (referer, None if referer else "{0.scheme}://{0.netloc}/".format(urllib.parse.urlparse(url))):
        headers = {"User-Agent": UA, "Accept": "image/avif,image/webp,image/*,text/html;q=0.9,*/*;q=0.8"}
        if ref:
            headers["Referer"] = ref
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                data = r.read(limit + 1)
                final = r.geturl()
            break
        except urllib.error.HTTPError as e:
            if e.code != 403 or ref or referer:
                raise
    if len(data) > limit:
        raise ValueError(f"{limit // 2**20}MBを超える画像は取り込めません")
    return ctype, data, final


def image_name(url, ctype, stem=None):
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
    base, ext = os.path.splitext(os.path.basename(path))
    if ext.lower() not in IMG_EXT:
        ext = CTYPE_EXT.get(ctype) or mimetypes.guess_extension(ctype) or ".png"
    return (stem or base or "web") + ext


def get_image(url, referer=None, stem=None):
    ctype, data, final = http_get(url, referer)
    if not ctype.startswith("image/"):
        raise ValueError("画像ではないURLでした (" + (ctype or "不明") + ")")
    return image_name(final, ctype, stem), data


def x_orig(url):
    """pbs.twimg.com の画像を原寸 (name=orig) に"""
    u = urllib.parse.urlparse(url)
    if u.netloc != "pbs.twimg.com" or not u.path.startswith("/media/"):
        return url
    path, _, _ = u.path.partition(":")  # 旧形式 .../ID.jpg:small
    q = dict(urllib.parse.parse_qsl(u.query))
    stem, ext = os.path.splitext(path)
    if ext and "format" not in q:
        q["format"] = ext[1:]
    q["name"] = "orig"
    return u._replace(path=stem, query=urllib.parse.urlencode(q)).geturl()


def fetch_pximg(url):
    """pixiv の縮小画像 URL → 原寸 (img-original は拡張子が分からないので順に試す)"""
    m = PXIMG_RE.match(url)
    if m:
        date, iid, page = m.groups()
        for ext in (".jpg", ".png", ".gif"):
            try:
                return get_image(f"https://i.pximg.net/img-original/img/{date}{iid}_p{page}{ext}", PIXIV,
                                 f"pixiv_{iid}_p{page}")
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    break
    return get_image(url, PIXIV)


def fetch_pixiv_work(iid):
    try:
        _, data, _ = http_get(f"https://www.pixiv.net/ajax/illust/{iid}/pages", PIXIV, 5 * 2**20)
        pages = json.loads(data)
        if pages.get("error"):
            raise ValueError(pages.get("message"))
    except Exception:
        raise ValueError("pixiv の作品を読めませんでした。ログインが必要な作品は、画像をコピーして Ctrl+V で追加してください")
    return [get_image(pg["urls"]["original"], PIXIV, f"pixiv_{iid}_p{i}")
            for i, pg in enumerate(pages["body"][:MAX_PAGES])]


def fetch_tweet(tid):
    """X のポスト URL → 添付画像 (原寸)。埋め込みウィジェット用の公開 API を使う"""
    try:
        _, data, _ = http_get(f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&lang=ja&token=a", None, 5 * 2**20)
        tw = json.loads(data)
    except Exception:
        raise ValueError("X のポストを読めませんでした (削除・鍵アカウント・センシティブ設定の可能性)")
    media = tw.get("mediaDetails") or []
    if not media and tw.get("quoted_tweet"):
        media = tw["quoted_tweet"].get("mediaDetails") or []
    urls = [m["media_url_https"] for m in media if m.get("media_url_https")]  # 動画はサムネイル
    if not urls:
        raise ValueError("このポストには画像がありません")
    return [get_image(x_orig(u), None, f"x_{tid}_{i + 1}") for i, u in enumerate(urls)]


class MetaImage(HTMLParser):
    """ページの代表画像 (og:image / twitter:image / image_src) を探す"""
    KEYS = ("og:image:secure_url", "og:image:url", "og:image", "twitter:image", "twitter:image:src")

    def __init__(self):
        super().__init__()
        self.found = {}

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): v for k, v in attrs if v}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in self.KEYS and a.get("content"):
                self.found.setdefault(key, a["content"])
        elif tag == "link" and (a.get("rel") or "").lower() == "image_src" and a.get("href"):
            self.found.setdefault("image_src", a["href"])

    def best(self):
        return next((self.found[k] for k in self.KEYS + ("image_src",) if k in self.found), None)


def fetch_url(url):
    """URL から画像を取り込む。[(ファイル名, 中身), ...] を返す"""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("http/https のURLだけ取り込めます")
    if m := PIXIV_WORK_RE.match(url):
        return fetch_pixiv_work(m.group(1))
    if m := TWEET_RE.match(url):
        return fetch_tweet(m.group(1))
    if url.startswith("https://i.pximg.net/"):
        return [fetch_pximg(url)]
    url = x_orig(url)
    ctype, data, final = http_get(url)
    if ctype.startswith("image/"):
        return [(image_name(final, ctype), data)]
    if "html" in ctype:  # 画像ではなくページだったら、そのページの代表画像を取る
        p = MetaImage()
        p.feed(data[:2 * 2**20].decode("utf-8", "replace"))
        img = p.best()
        if img:
            img = x_orig(urllib.parse.urljoin(final, img))
            if img.startswith("https://i.pximg.net/"):
                return [fetch_pximg(img)]
            return [get_image(img, final)]
        raise ValueError("このページから画像が見つかりませんでした")
    raise ValueError("画像ではないURLでした (" + (ctype or "不明") + ")")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    # ---- helpers ----
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BYTES:
            raise ValueError(f"{MAX_BYTES // 2**20}MBを超えるファイルは追加できません")
        return self.rfile.read(n)

    def read_json(self):
        return json.loads(self.read_body() or b"{}")

    def state(self):
        return {"items": list(DB["items"].values()), "boards": DB["boards"], "library": str(BASE),
                "cmyk_profile": CONV.name, "formats": sorted(IMG_EXT)}

    # ---- GET ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = APP_HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/ping":
            self.send_json({"app": "shiryo_box", "library": str(BASE)})
        elif path == "/api/state":
            with lock:
                scan()
                self.send_json(self.state())
        elif path.startswith("/img/"):
            rel = urllib.parse.unquote(path[5:])
            target = (IMAGES / rel).resolve()
            try:
                target.relative_to(IMAGES.resolve())
            except ValueError:
                return self.send_error(403)
            if not target.is_file():
                return self.send_error(404)
            it = next((i for i in DB["items"].values() if i["file"] == rel), None)
            if it:
                try:
                    target = view_file(it)
                except Exception as e:
                    print("変換できませんでした:", rel, e, flush=True)
                    return self.send_error(415)
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    # ---- POST ----
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/upload":
                name = urllib.parse.unquote(self.headers.get("X-Name") or "image.png")
                data = self.read_body()
                with lock:
                    item = store_bytes(data, name)
                    save_db()
                return self.send_json(item)

            if path == "/api/fetch":
                got = fetch_url(self.read_json().get("url", ""))
                with lock:
                    items = [store_bytes(data, name) for name, data in got]
                    save_db()
                return self.send_json({"items": items})

            body = self.read_json()
            if path == "/api/convert":
                cols = body.get("colors") or []
                out = CONV.rgb2cmyk(cols) if body.get("to") == "cmyk" else CONV.cmyk2rgb(cols)
                return self.send_json({"colors": out, "profile": CONV.name})
            with lock:
                if path == "/api/colors":
                    it = DB["items"].get(body.get("id"))
                    if it:
                        it["colors"] = body.get("colors") or []
                        fam = body.get("fam")
                        if isinstance(fam, dict):  # 大まかな色の系統ごとの割合
                            it["fam"] = {str(k)[:16]: float(v) for k, v in list(fam.items())[:32]}
                elif path == "/api/rename":
                    it = DB["items"].get(body.get("id"))
                    if it:
                        it["name"] = str(body.get("name", ""))[:200]
                elif path == "/api/delete":
                    TRASH.mkdir(parents=True, exist_ok=True)
                    for iid in body.get("ids", []):
                        it = DB["items"].pop(iid, None)
                        drop_cache(iid)
                        if it and (IMAGES / it["file"]).exists():
                            shutil.move(str(IMAGES / it["file"]), str(unique_path(TRASH, Path(it["file"]).name)))
                    for b in DB["boards"].values():
                        b["items"] = [x for x in b["items"] if x.get("id") in DB["items"]]
                elif path == "/api/board/save":
                    name = body.get("name")
                    if name in DB["boards"]:
                        DB["boards"][name] = body.get("board") or new_board()
                elif path == "/api/board/create":
                    name = str(body.get("name", "")).strip()[:50]
                    if name and name not in DB["boards"]:
                        DB["boards"][name] = new_board()
                elif path == "/api/board/delete":
                    DB["boards"].pop(body.get("name"), None)
                    if not DB["boards"]:
                        DB["boards"]["メイン"] = new_board()
                else:
                    return self.send_error(404)
                save_db()
                return self.send_json({"ok": True, "boards": DB["boards"]})
        except Exception as e:
            return self.send_json({"error": str(e)}, 400)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows の SO_REUSEADDR は使用中のポートにも重ねて bind できてしまうので切る
    allow_reuse_address = sys.platform != "win32"


def probe(port):
    """このポートで同じ資料フォルダの資料箱が動いていれば "new"、ping の無い旧版なら "old" """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path, kind in (("api/ping", "new"), ("api/state", "old")):
        try:
            with opener.open(f"http://127.0.0.1:{port}/{path}", timeout=2) as r:
                if json.loads(r.read()).get("library") == str(BASE):
                    return kind
        except Exception:
            pass
    return None


def alert(msg):
    print(msg)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "資料箱", 0x30)


def start_server():
    global DB, CONV
    IMAGES.mkdir(parents=True, exist_ok=True)
    DB = load_db()
    CONV = ColorConv()
    with lock:
        scan()
        save_db()
    try:
        server = Server(("127.0.0.1", PORT), Handler)
    except OSError:  # 別のアプリがポートを使っている
        server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/"


def open_folder(path):
    if hasattr(os, "startfile"):
        os.startfile(path)
    else:
        import subprocess
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def run_app(url, webview):
    """専用ウィンドウで開く。全部のウィンドウを閉じると終了"""

    class Api:
        def __init__(self):
            self._win = None  # 先頭 _ は pywebview が JS に公開しない

        def open_board(self, name):
            open_window(f"{url}?view=board&popup=1&pin=1&board={urllib.parse.quote(name)}", board=True)

        def set_on_top(self, on):
            on = bool(on)
            if sys.platform == "win32":
                # pywebview の on_top は JS 呼び出しのスレッドから使うと GIL を握ったまま
                # UI スレッドを待って固まるので、GIL を手放す ctypes で直接切り替える
                import ctypes
                hwnd = self._win.native.Handle.ToInt64()
                ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(-1 if on else -2),
                                                  0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # NOSIZE|NOMOVE|NOACTIVATE
            else:
                self._win.on_top = on
            return on

        def open_folder(self):
            open_folder(BASE)

    def open_window(u, board=False):
        api = Api()
        api._win = webview.create_window(
            "資料箱 - ボード" if board else "資料箱", u, js_api=api,
            width=520 if board else 1280, height=760 if board else 840, min_size=(320, 240),
            on_top=board, background_color="#17161a", text_select=True)

    open_window(url)
    storage = Path(os.environ.get("LOCALAPPDATA") or BASE) / "ShiryoBox" / "webview"
    webview.start(private_mode=False, storage_path=str(storage))


def main():
    if FROZEN and sys.stdout is None:  # ウィンドウアプリにはコンソールが無いのでログはファイルへ
        BASE.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(BASE / "shiryo_box.log", "a", encoding="utf-8", buffering=1)
    webview = None
    if "--browser" not in sys.argv:
        try:
            import webview
        except ImportError:
            pass
    kind = probe(PORT)
    if kind == "old":  # 2つのサーバーが同じ db.json に書くと資料の情報が消えるので止める
        return alert("古いバージョンの資料箱が同じ資料フォルダで起動中です。\n"
                     "黒い画面(起動_Windows.bat)を閉じてから、もう一度起動してください。")
    if kind == "new":
        url = f"http://127.0.0.1:{PORT}/"
        print("起動中の資料箱につなぎます:", url)
    else:
        url = start_server()
        print("資料箱 を起動しました:", url)
        print("資料フォルダ:", BASE)
    if webview:
        return run_app(url, webview)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    if DB is None:  # 既存のサーバーにブラウザをつないだだけ
        return
    print("終了するときはこの画面で Ctrl+C")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n終了しました")


DB = None
if __name__ == "__main__":
    main()
