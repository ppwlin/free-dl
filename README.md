# 免费全网下载后端 + iOS 快捷指令

一套自己掌控、无次数限制的解析后端，配合快捷指令实现「复制链接 → 下载 → 存相册」。

> 仅用于下载你有权保存的内容供个人观看，请勿二次发布或商用。

## 一、本地先跑起来

```bash
cd free-dl-backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

测试解析：

```bash
curl "http://127.0.0.1:8000/api/parse?url=<把分享链接放这里>"
```

返回示例：

```json
{
  "type": "video",
  "title": "...",
  "author": "...",
  "video_url": "/api/dl?u=https%3A%2F%2F...&r=...",
  "image_urls": [],
  "cover_url": "/api/dl?u=..."
}
```

- `type=video`：用 `video_url` 下载
- `type=images`：用 `image_urls` 逐张下载（图集）

## 二、部署到免费服务器

### 方案 1：Railway（最简单）
1. 把 `free-dl-backend` 整个目录推到你的 GitHub 仓库；
2. Railway → New Project → Deploy from GitHub repo，选中该仓库；
3. Railway 会自动识别 `Dockerfile` 构建；
4. Settings → 生成公开域名，得到形如 `https://xxx.up.railway.app`。

### 方案 2：Zeabur / Render
- Zeabur：New Project → 部署 Git 仓库，选择 Docker 类型即可；
- Render：New → Web Service → Docker 环境，启动命令已在 Dockerfile 中。

> 免费额度足够个人日常使用；服务闲置会自动休眠，首次访问会慢几秒，属正常。

### 方案 3：自己有 VPS / 群晖
```bash
docker build -t free-dl .
docker run -d -p 8000:8000 --name free-dl free-dl
```

## 三、怎么接 iOS 快捷指令

详细步骤见 `shortcut-guide.md`。

核心就三步：
1. 获取剪贴板 → 取 URL；
2. `获取 URL 内容`请求 `https://你的域名/api/parse?url=<剪贴板链接>`，类型选 JSON；
3. 按 `type` 分支：video 走 `保存到相册`，images 走 `重复`逐张保存。

## 四、常见问题

| 现象 | 原因与处理 |
|---|---|
| `upstream 403` | 平台防盗链，检查 `/api/dl` 是否带了 `r` 参数（referer） |
| `解析失败` | 抖音/小红书需要短链跳转，确认传入的是完整分享链接；部分内容需登录，属平台限制 |
| 返回 422 | 页面里没有直链，多为私密/已删除内容 |
| B 站 4K | 需要登录 Cookie，可在 `main.py` 的 ytdlp `opts` 中加 `cookiefile` |
| 图片存不进相册 | 快捷指令里用 `获取 URL 内容`后 `存储到相簿`，不要用「快速查看」 |

## 五、想加的功能

- **实况图**：小红书实况需要视频+封面两文件，快捷指令侧用 `制作实况照片` 拼接；
- **批量下载主页**：新增 `/api/parse-list?url=&limit=`，循环调用 yt-dlp 抓列表；
- **画质选择**：`/api/parse` 增加 `quality=1080` 参数，在 `ytdlp_parse` 里按高度筛选。
