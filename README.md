<div align="center">

# SafeRelay-Py

**双向 TG 私聊转发机器人 — 防骚扰 · 话题模式 · 本地题库验证**

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)](https://python.org)
[![Kurigram](https://img.shields.io/badge/Kurigram-Pyrogram%20Compat-purple?logo=telegram&logoColor=white)](https://github.com/KurimuzonAkuma/kurigram)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-red?logo=sqlite&logoColor=white)](https://www.sqlalchemy.org/)
[![License](https://img.shields.io/badge/License-GPL--3.0-green)](LICENSE)

</div>

---

## 简介

SafeRelay-Py 是 [SafeRelay](https://github.com/qianqi32/SafeRelay) 的 Python 移植版。原项目基于 JavaScript + Cloudflare Workers 运行，本版改用 **Kurigram (Pyrogram 兼容)** MTProto 客户端，并重构为三层解耦架构。

**核心功能**：用户私聊机器人 → 自动转发给管理员（私聊或话题群），管理员回复自动回传给用户。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **双向消息转发** | 用户 ↔ 管理员，支持文本/图片/文件/媒体组 |
| **论坛话题模式** | 每位访客自动创建话题，管理员在话题中回复即回传 |
| **本地题库验证** | 15 道中文选择题，InlineKeyboard 作答，防机器人骚扰 |
| **hCaptcha 验证** | 可选 Telegram Web App + hCaptcha 验证，适合公开入口 |
| **用户内容保护** | 发给普通用户的消息可启用 Telegram protect_content，限制复制/转发 |
| **编辑同步** | 用户和管理员编辑消息后自动同步更新 |
| **黑白名单** | 精准控制哪些用户可以跳过验证或禁止访问 |
| **联合封禁** | 接入第三方封禁系统，一次封禁全网拦截 |
| **欺诈检测** | 内置欺诈用户数据库，自动识别可疑用户 |
| **垃圾过滤** | 关键词/正则/链接数量多维过滤 |
| **速率限制** | 消息频率、验证尝试双重防护 |
| **管理面板** | `/menu` 一键操作，无需记忆命令 |
| **广播推送** | 向所有已验证用户群发消息 |
| **消息统计** | 每日消息数和活跃用户数 |
| **验证锁** | 并发验证保护，防止重复提交 |

---

## 架构

```
saferelay-py/
├── core/        第三方库封装层（Pyrogram / SQLAlchemy / httpx）
├── services/    业务逻辑层
├── handlers/    Telegram 事件路由
├── utils/       纯工具函数
```

**严格分层**：`handlers/` 和 `services/` 不得直接 import 第三方库，全部通过 `core/` 中转。

详见 [ARCHITECTURE.md](./ARCHITECTURE.md) 和 [STANDARDS.md](./STANDARDS.md)。

---

## 快速开始

### 环境要求

- Python 3.11+
- uv 包管理器

### 安装

```bash
git clone https://github.com/yyned2501/saferelay-py.git
cd saferelay-py

# 安装依赖
uv pip install -r requirements.txt
```

### 配置

复制 `.env.example` 并填入实际值：

```bash
cp .env.example .env
```

必需的环境变量：

| 变量 | 说明 | 示例 |
|------|------|------|
| `BOT_TOKEN` | Telegram Bot Token（必需） | `123456:ABC-DEF...` |
| `ADMIN_IDS` | 管理员用户 ID，逗号分隔（必需） | `123456789,987654321` |
| `GROUP_ID` | 论坛群组 ID（话题模式需要） | `-1001234567890` |
| `WELCOME_MSG` | 欢迎消息 | `欢迎你！请先完成验证` |
| `AUTOREPLY_MSG` | 自动回复 | `客服已收到您的消息` |
| `VERIFY_PROVIDER` | 验证方式：`quiz` 或 `hcaptcha` | `quiz` |
| `HCAPTCHA_SITE_KEY` | hCaptcha site key，`VERIFY_PROVIDER=hcaptcha` 时必需 | `10000000-ffff-ffff-ffff-000000000001` |
| `HCAPTCHA_SECRET` | hCaptcha secret，`VERIFY_PROVIDER=hcaptcha` 时必需 | `0x...` |
| `HCAPTCHA_WEBAPP_URL` | Telegram Web App 验证页 URL，完成后需 `sendData` 回传 token | `https://example.com/telegram-hcaptcha.html` |
| `PROTECT_USER_CONTENT` | 发给普通用户的消息是否禁止复制/转发 | `true` |

### hCaptcha Web App 回传格式

`HCAPTCHA_WEBAPP_URL` 指向的页面完成 hCaptcha 后，需要调用 Telegram Web App API：

```js
Telegram.WebApp.sendData(JSON.stringify({ hcaptcha_token: token }));
Telegram.WebApp.close();
```

Bot 收到 `web_app_data` 后会调用 hCaptcha `siteverify`，通过后自动处理用户验证前暂存的消息。

### 运行

```bash
uv run python3 main.py
```

### Docker 运行

```bash
cp .env.example .env
docker compose up -d --build
```

默认镜像名是 `gvddfdf/saferelay-py:latest`，也可以覆盖：

```bash
SAFERELAY_IMAGE=你的DockerHub用户名/saferelay-py:latest docker compose up -d
```

数据库和日志会分别保存到 Docker volume：`saferelay-data`、`saferelay-logs`。

### 自动发布到 Docker Hub

仓库已包含 GitHub Actions workflow：`.github/workflows/dockerhub.yml`。推送到 `main` 或创建 `v*.*.*` tag 时会自动构建并推送镜像。

需要在 GitHub 仓库中配置：

| 名称 | 类型 | 说明 |
|------|------|------|
| `DOCKERHUB_USERNAME` | Secret | Docker Hub 用户名 |
| `DOCKERHUB_TOKEN` | Secret | Docker Hub access token |
| `DOCKERHUB_IMAGE` | Variable（可选） | 完整镜像名，如 `gvddfdf/saferelay-py`；不填默认 `gvddfdf/saferelay-py` |

---

## 管理命令

所有命令在 Bot 私聊中使用，建议 **回复 (Reply)** 转发来的消息使用。

| 命令 | 作用 |
|:----:|:------|
| 直接回复 | 回复转发来的消息，内容自动回传给用户 |
| `/menu` | 打开图形化管理面板 |
| `/help` | 显示帮助信息 |
| `/ban` | 封禁用户 |
| `/unban` | 解封用户 |
| `/trust` | 信任用户（跳过验证） |
| `/untrust` | 取消信任 |
| `/reset` | 重置用户验证状态 |
| `/welcome` | 设置欢迎消息 |
| `/autoreply` | 设置自动回复 |
| `/broadcast` | 向所有已验证用户广播 |
| `/cleanup` | 清理失效的话题映射 |
| `/cachestats` | 查看缓存统计 |
| `/clearcache` | 清空缓存 |

---

## 话题模式

1. 在环境变量中设置 `GROUP_ID` 指向你的论坛群组
2. 确保机器人是群组管理员，拥有「管理话题」权限
3. 发送 `/menu` → 「转发模式」→ 选择「话题转发」

开启后，机器人为每位新访客自动创建话题，管理员在话题中回复即可回传给用户。

---

## 技术栈

| 组件 | 选型 |
|------|------|
| Telegram Client | Kurigram（Pyrogram 兼容分支） |
| 数据库 | SQLAlchemy 2.0 async + SQLite |
| HTTP | httpx.AsyncClient |
| 日志 | Python logging + 结构化输出 + 脱敏 |
| 运行环境 | Python 3.11+ / Linux / macOS / Windows |

---

## 许可证

[GNU General Public License v3.0](LICENSE)

基于 [SafeRelay](https://github.com/qianqi32/SafeRelay) 衍生，遵循 GPL-3.0。
