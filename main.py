"""免费全网视频/图集解析后端（个人使用）

端点：
  GET /api/parse?url=<分享链接>   解析出直链
  GET /api/dl?u=<直链>&r=<referer>  代理下载（带正确 UA/Referer，绕过防盗链）
  GET /               健康检查

只做解析与代理下载，不在服务器落地文件。
"""

import ipaddress
import os
import re
import socket
import tempfile
import time
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx
import yt_dlp
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

app = FastAPI(title="Free Downloader API", version="1.0")

# ---- Cookie 支持（可选，通过环境变量注入，用于需要登录态的平台）----
COOKIE_ENVS = {
    "douyin.com": "DOUYIN_COOKIE",
    "iesdouyin.com": "DOUYIN_COOKIE",
    "xiaohongshu.com": "XHS_COOKIE",
    "xhslink.com": "XHS_COOKIE",
    "bilibili.com": "BILI_COOKIE",
    "b23.tv": "BILI_COOKIE",
    "weibo.com": "WEIBO_COOKIE",
}


def cookie_file_for(url: str) -> str | None:
    """按目标站点从环境变量取 cookie，写成 Netscape 格式临时文件"""
    host = (urlparse(url).hostname or "").lower()
    env_name = None
    for dom, ev in COOKIE_ENVS.items():
        if host.endswith(dom):
            env_name = ev
            break
    if not env_name:
        return None
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return None
    # 兼容用户直接粘贴 "a=1; b=2" 或解码后的整段 cookie 串
    pairs = [p.strip() for p in raw.split(";") if "=" in p]
    if not pairs:
        return None
    domain = "." + host.split(".")[-2] + "." + host.split(".")[-1] if host.count(".") >= 1 else host
    lines = ["# Netscape HTTP Cookie File"]
    for p in pairs:
        k, v = p.split("=", 1)
        lines.append("\t".join([domain, "TRUE", "/", "FALSE", "0", k.strip(), v.strip()]))
    fd, path = tempfile.mkstemp(prefix="ck_", suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def cookie_header_for(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for dom, ev in COOKIE_ENVS.items():
        if host.endswith(dom):
            raw = (os.environ.get(ev) or "").strip()
            return raw or None
    return None

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# 短链域名：先跟随重定向拿到真实页面
SHORT_HOSTS = (
    "v.douyin.com", "xhslink.com", "t.cn", "b23.tv", "vm.tiktok.com",
    "vt.tiktok.com", "instagram.com", "youtu.be",
)

_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 600  # 秒


def _priv_ip(host: str) -> bool:
    """拒绝内网/回环地址，防 SSRF"""
    try:
        addr = socket.gethostbyname(host)
        ip = ipaddress.ip_address(addr)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except Exception:
        return True


def _ok_url(u: str) -> bool:
    p = urlparse(u)
    return p.scheme in ("http", "https") and bool(p.netloc) and not _priv_ip(p.hostname or "")


async def resolve_short(url: str) -> str:
    """跟随短链重定向，拿到真实地址"""
    if not any(h in url for h in SHORT_HOSTS):
        return url
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15, headers={"User-Agent": UA}) as c:
            r = await c.get(url)
            return str(r.url)
    except Exception:
        return url


LAST_ERROR: dict[str, str] = {}


def ytdlp_parse(url: str) -> dict[str, Any] | None:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "extractor_retries": 2,
        "retries": 3,
        "http_headers": {"User-Agent": UA, "Referer": url},
        # YouTube 需要 JS 运行时解签名，镜像内已装 deno
        "js_runtimes": {"deno": {}},
        "remote_components": {"ejs:github"},
    }
    ck = cookie_file_for(url)
    if ck:
        opts["cookiefile"] = ck
    ckh = cookie_header_for(url)
    if ckh and "http_headers" in opts:
        opts["http_headers"]["Cookie"] = ckh
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        LAST_ERROR[url] = f"{type(e).__name__}: {e}"[:600]
        return None
    finally:
        if ck and os.path.exists(ck):
            try:
                os.remove(ck)
            except Exception:
                pass

    if not info:
        return None
    if "entries" in info:
        info = next((e for e in info["entries"] if e), {})

    # 视频：优先带音频的 mp4
    video = None
    formats = info.get("formats") or []
    cands = [f for f in formats if f.get("vcodec") != "none" and f.get("url")]
    if cands:
        cands.sort(key=lambda f: (f.get("ext") != "mp4", -(f.get("height") or 0)))
        video = cands[0]["url"]
    if not video:
        video = info.get("url")

    # 图集
    images = []
    for e in (info.get("thumbnails") or [])[::-1]:
        if e.get("url"):
            images.append(e["url"])
            break
    for e in (info.get("entries") or []):
        if isinstance(e, dict) and e.get("url") and e.get("ext") in ("jpg", "jpeg", "png", "webp"):
            images.append(e["url"])

    return {
        "title": (info.get("title") or "").strip() or "untitled",
        "author": info.get("uploader") or info.get("channel") or "",
        "cover": info.get("thumbnail") or "",
        "video": video,
        "images": images,
        "referer": url,
    }


def html_parse(url: str) -> dict[str, Any] | None:
    """yt-dlp 失败时的兜底：抓页面里的图片/视频直链"""
    try:
        with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": UA}) as c:
            html = c.get(url).text
    except Exception:
        return None

    imgs = re.findall(r'https?://[^\s"\'\\]+\.(?:jpg|jpeg|png|webp)[^\s"\'\\]*', html)
    vids = re.findall(r'https?://[^\s"\'\\]+\.mp4[^\s"\'\\]*', html)
    title = re.search(r"<title>(.*?)</title>", html, re.S)

    imgs = list(dict.fromkeys(i.replace("&amp;", "&") for i in imgs))[:20]
    vids = list(dict.fromkeys(v.replace("&amp;", "&") for v in vids))[:5]

    if not imgs and not vids:
        return None
    return {
        "title": (title.group(1).strip() if title else "untitled"),
        "author": "",
        "cover": imgs[0] if imgs else "",
        "video": vids[0] if vids else None,
        "images": imgs,
        "referer": url,
    }


@app.get("/")
def health():
    return {"ok": True, "service": "free-dl", "time": int(time.time())}


@app.get("/api/parse")
async def parse(url: str = Query(..., description="分享链接")):
    url = url.strip()
    if not url.lower().startswith("http"):
        raise HTTPException(400, "url 必须是 http(s) 链接")

    key = url
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    real = await resolve_short(url)
    data = ytdlp_parse(real) or html_parse(real)
    if not data:
        raise HTTPException(422, "解析失败：链接可能需要登录、已删除，或平台暂不支持")

    if data.get("video") and not _ok_url(data["video"]):
        data["video"] = None
    data["images"] = [i for i in data["images"] if _ok_url(i)]

    if not data.get("video") and not data.get("images"):
        raise HTTPException(422, "未找到可下载的媒体文件")

    def proxied(u: str) -> str:
        return f"/api/dl?u={quote(u, safe='')}&r={quote(data['referer'], safe='')}"

    data["type"] = "video" if data.get("video") else "images"
    data["video_url"] = proxied(data["video"]) if data.get("video") else None
    data["image_urls"] = [proxied(i) for i in data["images"]]
    data["cover_url"] = proxied(data["cover"]) if data.get("cover") else None
    data.pop("video", None)
    data.pop("images", None)

    _cache[key] = (now, data)
    return data


@app.get("/api/debug")
def debug(url: str = Query(...)):
    """诊断端点：直接返回 yt-dlp 的原始报错，便于定位失败原因"""
    real_yt = None
    try:
        real_yt = ytdlp_parse(url)
    except Exception as e:
        LAST_ERROR[url] = f"{type(e).__name__}: {e}"[:600]
    return {
        "url": url,
        "ytdlp_ok": bool(real_yt),
        "ytdlp_error": LAST_ERROR.get(url, ""),
        "cookie_injected": bool(cookie_header_for(url)),
        "js_runtimes": "deno",
    }


@app.get("/api/dl")
def download(u: str = Query(...), r: str = Query("")):
    if not _ok_url(u):
        raise HTTPException(400, "非法地址")

    headers = {"User-Agent": UA}
    if r:
        p = urlparse(r)
        headers["Referer"] = r if p.scheme else "https://www.douyin.com/"

    # 流式转发，不在本地存文件
    client = httpx.Client(follow_redirects=True, timeout=60, headers=headers)
    try:
        resp = client.send(client.build_request("GET", u), stream=True)
    except Exception:
        raise HTTPException(502, "拉取媒体失败")

    if resp.status_code >= 400:
        resp.close()
        client.close()
        raise HTTPException(502, f"上游返回 {resp.status_code}")

    raw_name = urlparse(u).path.rsplit("/", 1)[-1] or "media"
    name = quote(raw_name[:120])

    def it():
        try:
            for chunk in resp.iter_bytes(64 * 1024):
                yield chunk
        finally:
            resp.close()
            client.close()

    return StreamingResponse(
        it(),
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{name}"},
    )
