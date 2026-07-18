# TradFi Funding 静态监控页

这是可直接部署的静态版本。访问者只会下载 HTML、CSS、JavaScript 和已经生成的
`data/dashboard.json`，不需要 Python 服务、开放端口或 Cloudflare Tunnel。

## 数据更新方式

GitHub Actions 每 8 小时执行一次：

1. 从 Binance、OKX、Gate、Bitget、KuCoin、Bybit、Phemex 的公开接口采集 TradFi 产品、funding 和 24h 成交额。
2. 使用统一统计区间生成套利候选。
3. 校验三个策略均有数据，且记录数没有异常骤降。
4. 更新 `data/dashboard.json` 并部署 GitHub Pages。

采集失败不会用空数据覆盖上一版。所有脚本只访问公开接口，不需要 API Key，也不要在网页仓库中保存交易密钥。

## 本地预览

在本目录运行：

```powershell
python -m http.server 8766
```

访问：

```text
http://127.0.0.1:8766/?strategy=cross_perp
```

不能直接双击 `index.html` 预览，因为浏览器通常禁止 `file://` 页面读取旁边的 JSON 文件。

## GitHub Pages

1. 将本目录作为一个新的 GitHub 仓库上传，默认分支使用 `main`。
2. 在仓库 `Settings > Pages` 中将 Source 设为 `GitHub Actions`。
3. 打开 `Actions`，手动执行一次 `Update and deploy TradFi dashboard`。
4. 工作流完成后，在 Pages 页面复制固定 HTTPS 地址。

定时任务使用 UTC，在每天 `00:17`、`08:17`、`16:17` 左右触发。GitHub 的计划任务可能有几分钟延迟。

## Cloudflare Pages

连接同一个 GitHub 仓库，配置：

```text
Build command: python scripts/build_site.py
Build output directory: dist
```

Cloudflare Pages 会在 `data/dashboard.json` 被工作流提交后自动重新部署。可绑定自己的域名；不配置 Access 或密码时，知道网址的人都能打开。

## 手动生成

采集脚本写入 `collectors/*/outputs`，然后运行：

```powershell
python scripts/generate_dashboard_data.py --refresh-turnover
python scripts/build_site.py
```

最终可部署文件位于 `dist`。
