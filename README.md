# ✈️ FlyClaw Web — 航班信息聚合查询图形界面

**FlyClaw Web** 是基于 [FlyClaw](https://github.com/AI4MSE/FlyClaw) 核心引擎构建的 **Web 图形化界面**，提供可视化配置管理与航班查询服务，同时也是一个完整的 REST API 航班查询服务器。

原生支持中英文，覆盖中国国内及国际航班。零 API Key 依赖，开箱即用。

> FlyClaw 是一个轻量级命令行工具，基于多源聚合架构，通过开源库及免费公开 API 获取数据。
> 核心价值：单一来源不稳定、不完整、覆盖有限 —— FlyClaw 的目标是 **聚合、去重、补齐、呈现**。

[English README](README_EN.md)

---

## 🚀 快速开始

### 环境要求

- Python 3.11+
- macOS / Linux

### 安装

```bash
# 克隆项目
git clone https://github.com/stellartraveler5162-SLCG/flyclaw-web.git
cd flyclaw-web

# 安装核心依赖
python3 -m pip install flask requests pyyaml curl_cffi cryptography --break-system-packages
```

### 启动 Web 服务器

```bash
python3 server.py                # 默认 http://0.0.0.0:8080
python3 server.py --port 9090    # 自定义端口
python3 server.py --debug        # 调试模式
```

启动后在浏览器打开 `http://127.0.0.1:8080`，即可看到图形界面。

---

## 📖 功能特性

### 🏠 首页仪表盘
- 系统状态概览（版本、活跃数据源数量、机场数据库规模）
- REST API 快速参考

### 🔍 航班查询（双模式）
| 模式 | 说明 | 示例 |
|------|------|------|
| **航班号查询** | 多源并发检索航班动态 | `CA981`、`MU5110` |
| **航线搜索** | 城市级智能展开 + 价格查询 | `上海` → `纽约` |

### 🏙️ 城市级智能搜索
输入城市名自动覆盖所有机场：

| 输入 | 自动展开 |
|------|----------|
| `上海` / `Shanghai` | PVG + SHA |
| `北京` / `Beijing` | PEK + PKX |
| `纽约` / `New York` | JFK + EWR + LGA |
| `东京` / `Tokyo` | NRT + HND |

### ⚙️ 图形化配置编辑器
- 可视化编辑 `config.yaml`，无需手动修改文件
- 六大数据源开关 / 优先级 / 超时 / 重试参数
- 查询设置、缓存设置、输出偏好
- `Ctrl+S` 快速保存，支持恢复默认值

### 📖 内置中文使用教程
- 9 章完整教程：快速开始 → 航班号查询 → 航线搜索 → 城市级搜索 → 往返 → 高级选项 → 配置管理 → API 调用 → 常见问题

### 🌐 REST API 服务器
可作为航班信息查询服务器，提供 7 个 API 端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/query/flight?flight=CA981` | 按航班号查询 |
| GET | `/api/search/route?from=PVG&to=JFK&date=2026-04-01` | 航线搜索（含价格）|
| GET | `/api/airports?q=上海` | 机场模糊搜索 |
| GET | `/api/airports/resolve?name=浦东` | 机场解析 |
| GET | `/api/config` | 读取配置 |
| POST | `/api/config` | 保存配置 |
| GET | `/api/info` | 服务器信息 |

---

## 📁 项目结构

```
flyclaw-web/
├── server.py              # Flask Web 服务器 + REST API
├── flyclaw.py             # 原始 CLI 查询引擎
├── config.py              # 配置加载器
├── config.yaml            # 默认配置文件
├── airport_manager.py     # 机场解析（7000+ 机场）
├── route_cache.py         # 航线缓存
├── templates/             # Web 前端页面
│   ├── index.html         # 首页仪表盘
│   ├── guide.html         # 中文使用教程
│   ├── config.html        # 图形化配置编辑器
│   └── query.html         # 航班查询界面
├── sources/               # 数据源插件
│   ├── fr24.py            # FlightRadar24
│   ├── google_flights.py  # Google Flights
│   ├── skiplagged.py      # Skiplagged
│   ├── fliggy_mcp.py      # 飞猪 MCP
│   └── airplaneslive.py   # Airplanes.live
├── cache/                 # 离线缓存数据
│   ├── airports.json      # 7000+ 机场数据库
│   └── airlines_iata_icao.json
└── img/                   # 截图资源
```

---

## 🖥️ 界面预览

| 首页 | 查询 | 配置 |
|------|------|------|
| 系统状态仪表盘 + API 文档 | 航班号/航线双模式查询 | 可视化编辑所有配置 |

---

## 🔌 数据源

| 数据源 | 说明 | 用途 |
|--------|------|------|
| **FlightRadar24** | 全球航班跟踪 | 航班动态、实时位置 |
| **Google Flights** | Google 航班搜索 | 航线搜索、价格 |
| **Skiplagged** | 隐藏城市票价 | 价格补充 |
| **飞猪 MCP** | 飞猪开放接口 | 中国国内航班价格 |
| **Airplanes.live** | ADS-B 实时数据 | 飞行器实时追踪 |

---

## 💡 查询优化建议

- 如果 Google 相关服务在您所在地区不可用，可在配置中关闭 Google Flights 数据源
- 减少「全局超时」和「提前返回时间」可加快响应
- 设置「优先数据源」（如 `fliggy_mcp`），一旦该源有结果立即返回

---

## 📄 开源协议

本项目基于 [Apache-2.0](LICENSE) 协议开源。

原 FlyClaw 项目：[github.com/AI4MSE/FlyClaw](https://github.com/AI4MSE/FlyClaw)

---

## ⚠️ 免责声明

- 本工具基于多源聚合架构，通过开源库及免费公开 API 获取数据
- 无需注册账号或提供任何 API Key 即可使用全部核心功能
- 本工具仅供学习研究用途，请遵守当地法律法规
- Google Flights 在部分地区（如中国大陆）可能不可用
- 价格数据来自多个数据源，不同来源的价格可能存在差异，仅供参考
- 程序不收集、不存储、不传输任何用户个人信息
