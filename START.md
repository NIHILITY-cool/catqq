# CatQQ Agent 启动与关闭指南

> 基于 NapCatQQ + AstrBot + Docker Compose 的 QQ 机器人。

## 前置条件

- Docker Desktop 已安装并运行
- 一个 QQ 小号（不建议用主号，有风控风险）
- 一个 AI 接口（DeepSeek、OpenAI、硅基流动等兼容 OpenAI 格式的均可）

## 首次启动

```bash
cd catqq-agent

# 1. 复制环境变量模板
cp .env.example .env

# 2. 编辑 .env，填入你的联系人和主动联系对象
#    CATQQ_CONTACTS=你的QQ号|主人|玖玖最重要的人
#    CATQQ_PROACTIVE_TARGET=主人

# 3. 拉取镜像并启动
#    Mac / Linux：
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d

#    Windows PowerShell：
docker compose up -d

# 4. 查看状态
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

期望看到两个容器都是 `Up`：

```
NAMES     STATUS          PORTS
napcat    Up X minutes    127.0.0.1:6099->6099/tcp
astrbot   Up X minutes    127.0.0.1:6185->6185/tcp
```

## 首次配置（只需做一次）

### 1. NapCat 扫码登录

打开 NapCat WebUI 扫码登录 QQ 小号：

```bash
# 查看 WebUI 地址和 Token
docker logs napcat 2>&1 | grep "WebUi Token"
```

浏览器打开日志中显示的 URL，用 QQ 小号扫码。

### 2. AstrBot 后台配置

打开 `http://127.0.0.1:6185`，用户名 `astrbot`，初始密码查日志：

```bash
docker logs astrbot 2>&1 | grep "Initial password"
```

**2a. 创建 OneBot v11 机器人：**
- Bots → 创建 → 平台选 OneBot v11
- Reverse WebSocket Host：`0.0.0.0`
- Reverse WebSocket Port：`6199`
- Reverse WebSocket Path：`/ws`
- Token 留空 → 保存并启用

**2b. 添加 AI 模型（以 DeepSeek 为例）：**
- Providers → 添加 → OpenAI Compatible
- API Base URL：`https://api.deepseek.com`
- API Key：你的 DeepSeek API Key
- Model：`deepseek-v4-flash`
- 保存，去 Config 设为默认模型

**2c. 配置人格（Persona）：**
- Persona → 创建
- 写你想让机器人扮演的角色，或使用项目自带的 `persona.md` 作为参考

### 3. 验证连接

```bash
docker logs astrbot 2>&1 | grep "适配器已连接"
```

看到 `aiocqhttp(OneBot v11) 适配器已连接` 即成功。

用白名单 QQ 给机器人发消息，收到 AI 风格回复即一切正常。

---

## 日常使用

### 启动

```bash
cd catqq-agent
# Mac / Linux
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d
# Windows
docker compose up -d
```

### 查看日志

```bash
# 实时日志
docker logs -f napcat &
docker logs -f astrbot

# 最近日志
docker logs astrbot --tail 50
docker logs napcat --tail 50
```

### 重启单个服务

```bash
# 改了插件代码 → 只重启 AstrBot
docker restart astrbot

# NapCat 掉线 → 重启 NapCat（可能需要重新扫码）
docker restart napcat
```

### 停止

```bash
docker compose down
```

---

## 数据持久性

| 数据 | 存储位置 | 重启后 |
|------|---------|--------|
| 对话历史 | `./data/` SQLite 数据库 | ✅ 保留 |
| AI 人设 | `./data/` SQLite 数据库 | ✅ 保留 |
| 插件代码 | `./data/plugins/` | ✅ 保留 |
| QQ 登录态 | `./ntqq/` | ⚠️ 保留，偶尔需重新扫码 |

- `docker compose down` 不丢数据
- `docker compose down -v` **会丢数据**
- 手动删 `./data/` 或 `./ntqq/` 会丢对应的数据

### 备份

```bash
tar -czvf catqq-backup-$(date +%Y%m%d).tar.gz \
  data/data_v4.db data/plugins/ data/cmd_config.json \
  napcat/config/ docker-compose.yml .env.example persona.md
```

---

## 更新人设

编辑 `persona.md`，然后运行：

```bash
python3 << 'PYEOF'
import sqlite3
with open('persona.md', 'r') as f:
    content = f.read()
content = content.replace('# 小猫"玖玖"人设\n\n', '')
db = sqlite3.connect('data/data_v4.db')
db.execute("UPDATE personas SET system_prompt = ?, updated_at = datetime('now') WHERE persona_id = '玖玖'", [content])
db.commit()
db.close()
PYEOF
docker restart astrbot
```

> 如果你用的是其他人格名字，把 `persona_id = '玖玖'` 改成你的人格 ID。

---

## 清理对话历史

```bash
python3 << 'PYEOF'
import sqlite3
db = sqlite3.connect('data/data_v4.db')
db.execute("DELETE FROM conversations")
db.execute("DELETE FROM platform_message_history")
db.execute("DELETE FROM platform_sessions")
db.commit()
db.close()
PYEOF
docker restart astrbot
```

---

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `docker: Cannot connect to the Docker daemon` | Docker Desktop 没运行 | 打开 Docker Desktop |
| NapCat 日志"连接意外关闭" | AstrBot 还没启动完 | 等 30 秒自动重连 |
| 发了消息没回复 | WebSocket 断连 | 检查 `docker logs astrbot \| grep 适配器已连接` |
| 扫码后还是离线 | QQ 风控 | 等半小时再试 |
| 消息反复轰炸 | `<system_reminder>` 未过滤 | 确认插件已更新到最新版 |

---

## 自定义

### 换 AI 模型

在 AstrBot WebUI 的 Providers 页面添加新的提供商即可。任何兼容 OpenAI Chat Completions 格式的 API 都能用（DeepSeek、硅基流动、Groq、OpenAI 等）。

### 换人格

编辑 `persona.md`，或直接在 AstrBot WebUI 的 Persona 页面新建。人格就是告诉 AI"你是谁、怎么说话"，长短皆可，几十字到几千字都能运行。

### 换联系人和主动联系对象

编辑 `.env`：

```bash
# QQ号|名字|关系，多个联系人用英文逗号分隔
CATQQ_CONTACTS=326开头QQ号|蛋蛋|创造玖玖的人,另一个QQ号|鲍鲍|玖玖的小主人

# 可以填联系人名字，也可以填 QQ 号
CATQQ_PROACTIVE_TARGET=鲍鲍
```

`CATQQ_CONTACTS` 同时控制白名单和身份识别。插件会自动把消息标成 `(这是鲍鲍（玖玖的小主人）)` 这种形式再交给 AI。

主动联系功能的默认限制：
- 10:00-23:00 才会主动发
- 目标刚聊过 60 分钟内不会主动发
- 距离上次主动联系至少 3 小时
- 每天最多主动联系 3 次
- 超过 6 小时没聊天会触发"久未聊天"类型

白名单用户也可以让小猫现在或到点后提醒某个联系人：

```text
提醒鲍鲍下午考试带笔
叫 鲍鲍：吃饭前看一眼准考证
去问问鲍鲍吃药没
半小时后叫鲍鲍喝水
下午三点问问鲍鲍吃药没
明早八点提醒鲍鲍带药
找鲍鲍
```

不写时间就是现在发；写了时间会存成任务，到点后自动发。支持"现在/马上/立刻"、"10分钟后/半小时后/2小时后"、"下午三点/今晚九点/明早八点/明天下午三点"这类表达。

小猫会直接私聊目标联系人，并给发起人回复确认。这个任务不会交给 AI 猜，避免出现"鲍鲍没找我"这类误判。

改完后重启 AstrBot：

```bash
docker restart astrbot
```

### 加功能

插件入口在 `data/plugins/astrbot_plugin_cat_guard/main.py`，主动联系的纯逻辑在同目录的 `proactive.py`。当前包含：
- 白名单守卫
- 睡觉/醒醒
- 早安晚安定时消息
- 身份识别注入
- 主动联系对象
- 手动提醒联系人

新功能直接在这个文件加，或在 `data/plugins/` 下新建插件目录。

### 换 OneBot 实现

当前用 NapCatQQ 做 QQ 端。如果将来想换（比如用 LLOneBot），只需改 docker-compose 里的 napcat 服务和 OneBot 连接配置。AstrBot 这边不需要改任何东西——OneBot v11 是标准协议。
