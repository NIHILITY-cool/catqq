# CatQQ Agent 技术原理

本文从头到尾解释这个项目是如何工作的——不只是"用了什么"，而是"做了什么、怎么做的"。

---

## 目录

1. [整体架构](#1-整体架构)
2. [第一层：NapCatQQ — 如何模拟一个 QQ 客户端](#2-第一层napcatqq--如何模拟一个-qq-客户端)
3. [第二层：OneBot v11 — 消息的通用语言](#3-第二层onebot-v11--消息的通用语言)
4. [第三层：WebSocket 通信 — 消息如何跨越容器](#4-第三层websocket-通信--消息如何跨越容器)
5. [第四层：AstrBot — AI 调度中心](#5-第四层astrbot--ai-调度中心)
6. [第五层：AI 模型调用 — 小猫如何说话](#6-第五层ai-模型调用--小猫如何说话)
7. [第六层：插件系统 — 白名单、睡觉、定时消息](#7-第六层插件系统--白名单睡觉定时消息)
8. [消息分段回复](#8-消息分段回复)
9. [Docker 容器编排](#9-docker-容器编排)
10. [一条消息的完整旅程](#10-一条消息的完整旅程)
11. [身份标识注入](#11-身份标识注入)
12. [人设工程](#12-人设工程)
13. [分段回复](#13-分段回复)
14. [插件 cat_guard 详解](#14-插件-cat_guard-详解)
15. [常见问题与解决](#15-常见问题与解决)
16. [当前配置快照](#16-当前配置快照)
17. [对话历史管理](#17-对话历史管理)
    - [附录：关键文件速查](#附录关键文件速查)

---

## 1. 整体架构

```
手机QQ / PC QQ
    ↕ QQ 私有协议 (NTQQ)
┌──────────────────────────────┐
│ NapCatQQ 容器                 │
│ - 登录QQ账号                   │
│ - 收发QQ消息                   │
│ - 把QQ消息翻译成 OneBot v11 格式│
└──────────┬───────────────────┘
           │ OneBot v11 / WebSocket
           │ ws://astrbot:6199/ws
┌──────────▼───────────────────┐
│ AstrBot 容器                   │
│ - 接收消息事件                  │
│ - 插件过滤 (白名单/睡觉)         │
│ - 拼接人格 + 历史 → 调 AI       │
│ - 分段回复                      │
│ - 定时任务 (早安晚安)            │
└──────────┬───────────────────┘
           │ HTTPS (OpenAI 兼容 API)
┌──────────▼───────────────────┐
│ DeepSeek V4 Flash             │
│ - 根据 system prompt + 对话    │
│   生成小猫风格的回复             │
└──────────────────────────────┘
```

两个容器通过 Docker 内部桥接网络通信，不与公网交互。唯一出公网的流量是：NapCat ↔ 腾讯 QQ 服务器、AstrBot ↔ DeepSeek API。

---

## 2. 第一层：NapCatQQ — 如何模拟一个 QQ 客户端

### 2.1 问题

腾讯没有为第三方机器人提供 QQ 消息 API。要收发 QQ 消息，你必须像一个真正的 QQ 客户端一样与腾讯服务器通信。

### 2.2 QQ 的通信协议：NTQQ

QQ 客户端与服务器之间使用一套私有二进制协议通信，这套协议叫 **NTQQ**（New Technology QQ）。它是 QQ 桌面版和移动版都在使用的底层协议。协议内容包括：

- **登录认证**：密码登录、二维码登录、token 刷新。二维码登录的原理是客户端向服务器请求一个临时 token，服务器生成二维码（token 编码后的 URL），手机 QQ 扫码后服务器将 token 与登录态绑定，客户端轮询 token 状态直到登录成功。
- **消息收发**：私聊消息、群聊消息、图片、语音等，每种消息类型有对应的二进制编码格式。
- **心跳保活**：客户端定期向服务器发送心跳包，证明自己在线。如果长时间不发送心跳，服务器会断开连接，账号变为离线状态。
- **好友/群组管理**：好友列表同步、加好友、群成员列表等。

### 2.3 NapCat 做了什么

NapCatQQ 本质上是一个**无界面的 QQ 客户端**。它的核心是 QQ 官方桌面客户端的底层通信库（`libNapCat`），但去掉了 GUI 部分，暴露为可编程接口。

关键步骤：

1. **加载 QQ 原生库**：NapCat 加载 QQ 桌面客户端中的 `libNapCat.so`/`.dll`，这些库包含了 NTQQ 协议的完整实现——登录、消息加密解密、协议编解码等。NapCat 不是自己逆向实现协议，而是复用 QQ 自己的库，因此行为与官方客户端一致，降低了封号风险。

2. **注入适配层**：NapCat 在原生库之上注入了一层 Hook，拦截关键事件（收到消息、登录状态变化等），并暴露为 WebSocket/HTTP API。

3. **提供 OneBot v11 接口**：NapCat 内部有一个协议适配器，将 QQ 原生事件翻译成 OneBot v11 标准格式（见下一节）。

4. **无头运行**：NapCat 不需要 GUI，所有操作通过 WebUI（`http://127.0.0.1:6099/webui`）或 API 完成。扫码登录时，它在内存中生成二维码并在 WebUI 上显示，扫描后完成认证。

### 2.4 NapCat 在容器里做什么

在 Docker 容器中，NapCat 进程持续运行，做三件事：

- **维持 QQ 在线状态**：周期性的心跳包、协议层保活。登录态保存在 `ntqq/` 目录（容器内为 `/app/.config/QQ`），包括 session token、设备指纹等。容器重启后如果这个目录还在且有有效 token，可以免扫码登录。
- **监听 QQ 消息**：当有人给机器人发私聊或群聊消息时，QQ 服务器推送消息到 NapCat，NapCat 解析后生成 OneBot v11 事件。
- **转发到 AstrBot**：通过 WebSocket 将 OneBot v11 事件发给 AstrBot，同时通过同一个 WebSocket 接收 AstrBot 发来的回复，转成 QQ 消息发出。

---

## 3. 第二层：OneBot v11 — 消息的通用语言

### 3.1 为什么要有一个中间协议

如果每个 IM 平台（QQ、微信、Telegram、Discord）的消息格式都不同，那么换一个 IM 平台就要重写整个机器人逻辑。OneBot 解决这个问题：定义一个统一的消息格式，各平台的适配器负责"平台消息 ↔ OneBot 格式"的翻译。

### 3.2 OneBot v11 的消息模型

OneBot v11 定义了两类核心通信：

**事件（Event）**：平台 → 机器人框架

```json
{
  "time": 1783077235,
  "self_id": 3757296370,
  "post_type": "message",
  "message_type": "private",
  "sub_type": "friend",
  "user_id": 3262379680,
  "message": [
    {"type": "text", "data": {"text": "你好"}}
  ],
  "sender": {
    "user_id": 3262379680,
    "nickname": "Nihility"
  }
}
```

每个字段的含义：
- `self_id`：机器人自己的 QQ 号（谁收到了这条消息）
- `user_id`：发送者的 QQ 号
- `message_type: "private"`：这是一条私聊消息（而非群聊 `group`）
- `message`：消息内容，是一个数组。每条消息由多个"段"组成，文本是 `type: "text"`，图片是 `type: "image"`。这样一条消息可以同时包含文字和图片。

**动作（Action / API 调用）**：机器人框架 → 平台

```json
{
  "action": "send_private_msg",
  "params": {
    "user_id": 3262379680,
    "message": [
      {"type": "text", "data": {"text": "人，你来了呀。"}}
    ]
  }
}
```

机器人框架调用 `send_private_msg` 这个动作，平台适配器收到后翻译成 QQ 协议的发消息操作。

### 3.3 其他常用事件和动作

| 事件 | 含义 |
|------|------|
| `message.private.friend` | 好友私聊消息 |
| `message.group.normal` | 群聊普通消息 |
| `notice.friend_add` | 新增好友通知 |
| `meta_event.heartbeat` | 心跳包 |

| 动作 | 含义 |
|------|------|
| `send_private_msg` | 发私聊消息 |
| `send_group_msg` | 发群聊消息 |
| `get_friend_list` | 获取好友列表 |
| `delete_msg` | 撤回消息 |

---

## 4. 第三层：WebSocket 通信 — 消息如何跨越容器

### 4.1 为什么用 WebSocket

HTTP 是"请求-响应"模式——客户端发请求，服务器返回响应。但 QQ 消息是**服务器主动推送**的——你不知道对方什么时候发消息。如果每秒钟轮询一次"有新消息吗？"既浪费资源又延迟高。

WebSocket 是一个**全双工长连接**：连接建立后，双方随时可以给对方发数据，不需要等待对方先请求。这正是 IM 消息场景需要的。

### 4.2 正向 vs 反向 WebSocket

**正向 WebSocket**（Server 模式）：
```
AstrBot (客户端) --连接--> NapCat (服务端，监听端口)
```
NapCat 开一个端口等待连接，AstrBot 主动连过去。这种模式的问题是：如果 NapCat 在 NAT 后面（比如家庭网络），AstrBot 可能连不到它。

**反向 WebSocket**（Reverse / Client 模式，本项目的选择）：
```
NapCat (客户端) --连接--> AstrBot (服务端，监听 6199 端口)
```
AstrBot 开端口等待连接，NapCat 主动连过去。这样做的好处是：AstrBot 是"中心"，NapCat 无论如何部署都能找到 AstrBot。

### 4.3 本项目中的 WebSocket 流向

1. AstrBot 在端口 `6199` 上启动 WebSocket 服务器，路径为 `/ws`
2. NapCat 作为 WebSocket 客户端，连接到 `ws://astrbot:6199/ws`（`astrbot` 是 Docker 内部 DNS 解析的容器名）
3. 连接建立后：
   - **NapCat → AstrBot**：QQ 消息事件（OneBot v11 格式的 JSON）
   - **AstrBot → NapCat**：API 调用（如 `send_private_msg`），在同一条 WebSocket 连接上返回
4. 连接断开时，NapCat 每 30 秒重试一次

### 4.4  Docker 网络如何让两个容器通信

Docker Compose 创建了一个名为 `astrbot_network` 的桥接网络。在这个网络中：

- 每个容器获得一个内部 IP（如 `172.18.0.2`、`172.18.0.3`）
- Docker 内置的 DNS 服务器让容器可以通过**容器名**互相解析——`napcat` 解析为 NapCat 容器的 IP，`astrbot` 解析为 AstrBot 容器的 IP
- 所以 NapCat 配置中写的 `ws://astrbot:6199/ws` 能从容器内部解析到正确的 IP
- 端口 `6099` 和 `6185` 通过 `127.0.0.1:6099:6099` 映射到宿主机，只在本地可访问，不暴露到公网

---

## 5. 第四层：AstrBot — AI 调度中心

### 5.1 AstrBot 是什么

AstrBot 是一个消息机器人框架。它的核心职责是：

```
接收消息 → 过滤 → 构建 prompt → 调 AI → 处理回复 → 发回平台
```

### 5.2 AstrBot 的消息处理管道

当一条 OneBot v11 消息事件到达 AstrBot 时，它经过以下管道：

**Stage 1：平台适配器接收**

`aiocqhttp`（AstrBot 内置的 OneBot v11 适配器）解析 JSON 事件，提取发送者 ID、消息内容、消息类型等信息，封装为 `AstrMessageEvent` 对象。

**Stage 2：插件链处理**

AstrBot 的插件系统基于 `Star` 类。每个已加载的插件可以注册事件处理器。消息事件按注册顺序依次传给每个插件的处理器。插件可以：
- 修改消息内容
- 调用 `event.stop_event()` 阻止后续处理（我们的白名单守卫就用这个）
- 通过 `yield event.plain_result(...)` 直接回复（不经过 AI）
- 什么都不做，让消息继续传递

我们的 `cat_guard` 插件按以下顺序判断：

```
收到消息
  → 是群聊？ → stop_event()，不回复
  → 发送者不在白名单？ → stop_event()，不回复
  → 内容是"小猫睡觉"？ → 回复"咪睡了，人晚安"，stop_event()
  → 内容是"小猫醒醒"？ → 回复小猫风格的唤醒语，stop_event()
  → 当前在睡觉状态？ → stop_event()，不回复
  → 以上都不满足 → 放行，交给 AI 处理
```

**Stage 3：对话管理**

消息通过插件链后，AstrBot 的对话管理器负责：

- 查找或创建该用户的会话（conversation），每个用户一个会话 ID
- 加载会话历史（之前的所有对话），作为 LLM 的上下文
- 加载当前生效的人格（Persona），作为 system prompt
- 将当前消息追加到会话历史

**Stage 4：LLM 调用**

见下一节。

**Stage 5：回复处理**

收到 LLM 的回复后：
- 如果开启了分段回复且回复超过字数阈值，按标点拆分为多句
- 每句之间按配置的间隔时间（0.5~1.5 秒）依次发送
- 将回复追加到会话历史

### 5.3 会话管理

AstrBot 使用 SQLite 数据库（`data/data_v4.db`）存储：

- **对话历史**（`conversations` 表）：每个会话的完整 JSON 消息数组，包括 `user` 和 `assistant` 两种角色的消息
- **人格**（`personas` 表）：用户创建的 Persona 内容
- **平台消息历史**（`platform_message_history` 表）：原始消息记录

AstrBot 维护一个 20 轮的上下文窗口。超出窗口的旧消息会被截断，这样既能维持对话连贯性，又不会因为上下文过长导致 token 浪费和 API 调用变慢。

---

## 6. 第五层：AI 模型调用 — 小猫如何说话

### 6.1 调用流程

AstrBot 通过 OpenAI 兼容的 Chat Completions API 调用 DeepSeek。实际的 HTTP 请求如下：

```
POST https://api.deepseek.com/v1/chat/completions
Authorization: Bearer sk-你的DeepSeek-API-Key
Content-Type: application/json

{
  "model": "deepseek-v4-flash",
  "messages": [
    {
      "role": "system",
      "content": "你现在扮演一只会在 QQ 上和人聊天的小猫，名字叫"玖玖"..."
    },
    {
      "role": "user",
      "content": "你好"
    },
    {
      "role": "assistant",
      "content": "人。你来了。"
    },
    {
      "role": "user",
      "content": "在干嘛"
    }
  ],
  "temperature": 0.8,
  "max_tokens": 1024
}
```

### 6.2 Prompt 构建

AI 看到的上下文由三部分拼接而成：

**System Prompt（系统消息）**：我们设置的小猫人格。它告诉 AI "你是谁、怎么说话、什么能做、什么不能做"。这是一条 `role: "system"` 的消息，放在 `messages` 数组的第一位。AI 模型会将 system prompt 作为最高优先级的指令来遵守。

**History（历史消息）**：数据库中该会话最近的 N 轮对话。格式是交替的 `role: "user"` 和 `role: "assistant"` 消息。AI 通过这段历史理解对话的上下文——之前聊了什么、现在在说什么话题。

**Current Message（当前消息）**：用户刚发的最新一条消息，格式是 `role: "user"`。

三条拼接起来形成完整的 `messages` 数组，发给 DeepSeek API。

### 6.3 角色对应关系

| QQ 中的角色 | API 中的 role | 含义 |
|------------|--------------|------|
| 小猫人格设定 | `system` | 告诉 AI 它的身份和行为准则 |
| 对方发的消息 | `user` | 对方的发言 |
| 小猫的回复 | `assistant` | AI 生成的回复 |

### 6.4 AI 如何维持人设

人设不是"魔法"。System prompt 本身就是一段文字，AI 在每个 token 生成时都会"看到"它。但因为 transformer 模型的注意力机制对序列开头的内容权重会自然衰减，长对话中 system prompt 的影响可能被稀释。

AstrBot 的应对策略：
- 上下文窗口限制在 20 轮，避免对话过长后 system prompt "沉底"
- 人设中明确写了大量具体场景的处理方式（"对方说在忙 → 装懂事但有点委屈"），比笼统的"你要可爱"有效得多
- 对于关键行为（如被问是不是 AI），人设里写了精确的回复模板，AI 会优先匹配

### 6.5 DeepSeek V4 Flash

`deepseek-v4-flash` 是一个 284B 参数的 MoE（混合专家）模型，每次推理实际激活约 13B 参数。支持 1M token 的上下文窗口。API 兼容 OpenAI 格式，所以 AstrBot 使用 OpenAI Compatible 提供商类型即可接入。

---

## 7. 第六层：插件系统 — 白名单、睡觉、定时消息

### 7.1 AstrBot 插件机制

AstrBot 的插件系统基于 Python 的异步事件驱动模型。每个插件是一个继承 `Star` 的类，通过装饰器 `@filter.event_message_type()` 注册事件处理器。

插件放在 `data/plugins/<插件名>/` 目录下，必须包含 `metadata.yaml`（声明插件名、版本、支持的平台）和一个入口 `.py` 文件。

AstrBot 在启动时扫描插件目录，动态加载每个插件。插件的 `__init__` 方法在加载时执行，`terminate` 方法在卸载/停止时执行。

### 7.2 cat_guard 插件详解

**白名单守卫**

核心是 `ALLOWED_USERS` 集合。新版优先从 `CATQQ_CONTACTS` 读取联系人配置，旧版 `CATQQ_ALLOWED_USERS` 仍然兼容。`event.get_sender_id()` 返回的是 OneBot v11 事件中的 `user_id` 字段（QQ 号）。不在集合内的：`event.stop_event()` 阻止事件继续传递，AI 不会被调用，消息零成本过滤。

```python
if user_id not in ALLOWED_USERS:
    event.stop_event()
    return
```

**睡觉 / 醒醒**

一个布尔标志 `self.sleeping` 控制状态切换。为什么用 `if "小猫睡觉" in message:` 而不是精确匹配？因为 QQ 消息可能带标点（"小猫睡觉。"），子串匹配更宽容。

睡觉和醒醒的判断在"睡觉状态检查"之前，这保证了：即使已经在睡觉，发"小猫醒醒"依然能被唤醒。这是一个有意的优先级设计。

**定时早安晚安**

实现原理是真·while True 循环：

```python
async def _start_scheduler(self):
    await asyncio.sleep(10)  # 等平台适配器初始化
    while True:
        # 检查当前小时是否匹配
        # 用日期标记防止同一小时内重复发送
        await asyncio.sleep(60)  # 每分钟检查一次
```

为什么不用 cron？因为这个循环运行在 AstrBot 的 asyncio 事件循环内部，可以访问 `self.context`（平台适配器、数据库等），不需要外部进程。`asyncio.sleep(60)` 在 try 块外面，这样 `terminate()` 发出的 `CancelledError` 能干净地终止循环。

发送消息用的是 `platform.send_by_session()`，session ID 格式是 `aiocqhttp:PrivateMessage:<QQ号>`（UMO 格式：平台:消息类型:会话ID）。

### 7.3 `stop_event()` 的工作原理

AstrBot 的事件系统用了一个事件总线。当消息到达时，事件对象在插件链中传递。每个插件处理完后，事件继续传递给下一个插件。`stop_event()` 在当前插件处理完后设置一个标志，告诉总线不要再把事件传递给后续插件，也不会进入 LLM 调用阶段。这保证了：

- 非白名单用户的消息不会触发 AI 调用，零 token 消耗
- 睡觉状态下的消息静默处理
- 但睡觉/醒醒命令本身仍然能触发回复（因为在白名单检查之后、睡觉检查之前被拦截）

---

## 8. 消息分段回复

### 8.1 为什么需要分段

AI 有时会返回多句话（例如"人。小猫刚刚趴着发呆。你一来，尾巴就醒了。"）。如果一次性发出，不像真人聊天。分段回复按标点拆成多句，每句单独发一条 QQ 消息，模拟真人打字节奏。

### 8.2 怎么实现的

AstrBot 的 `respond.stage` 在 LLM 回复后：

1. 检查回复总字数是否超过 `words_count_threshold`（150 字）
2. 如果超过，用正则 `.*?[。？！~…]+|.+$` 提取每一句
3. 对每一句：
   - 调用平台适配器的 `send_message()` 单独发送
   - 随机等待 0.5~1.5 秒再发下一句
4. 如果没超过阈值，正常一次性发出

这就是你看到的长回复自动分成多条的效果。

---

## 9. Docker 容器编排

### 9.1 为什么两个容器

NapCatQQ 和 AstrBot 是两个独立的进程，有不同的依赖（NapCat 需要 Node.js 和 QQ 原生库，AstrBot 需要 Python 和 AI 库）。Docker 把它们各自打包在独立的镜像中，避免依赖冲突。

### 9.2 数据如何共享

两个容器通过 `docker-compose.yml` 中的 volumes 共享同一个宿主机目录：

```
./data → /AstrBot/data（两个容器都能读写）
```

这意味着：
- 插件代码放在宿主机 `./data/plugins/` 下，容器内就能看到
- AstrBot 写的数据库，NapCat 如果需要也能读
- 修改插件代码后重启 AstrBot 即可生效，不需要重新构建镜像

### 9.3 启动命令解释

```bash
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d
```

- `NAPCAT_UID=$(id -u)`：把当前 Mac 用户的 UID 传给容器，让容器内的文件权限与宿主机一致
- `NAPCAT_GID=$(id -g)`：同上，用户组 ID
- `docker compose up -d`：读取 `docker-compose.yml`，拉取镜像（如果本地没有），创建容器，启动，`-d` 表示后台运行

---

## 10. 一条消息的完整旅程

以下跟踪一条"你好"消息从头到尾经过的所有环节：

```
1. 好友在 QQ 上给机器人（3757296370）发"你好"
   ↓
2. 腾讯 QQ 服务器将消息推送到 NapCat 容器
   （通过 NTQQ 协议的 TCP 长连接，NapCat 一直连着 QQ 服务器）
   ↓
3. NapCat 解析 NTQQ 二进制数据包，提取：
   - 发送者 QQ：3262379680
   - 消息类型：私聊文本
   - 消息内容："你好"
   ↓
4. NapCat 的 OneBot 适配器将消息翻译成 JSON 事件：
   {"post_type":"message", "message_type":"private",
    "user_id":3262379680, "message":[{"type":"text","data":{"text":"你好"}}]}
   ↓
5. NapCat 的 WebSocket 客户端通过 ws://astrbot:6199/ws
   将 JSON 事件发送给 AstrBot
   ↓
6. AstrBot 的 aiocqhttp 适配器接收事件，封装为 AstrMessageEvent
   ↓
7. cat_guard 插件处理：
   a. 检查 group_id → 空，不是群聊，通过
   b. 检查 user_id=3262379680 是否在白名单 → 是，通过
   c. 检查消息是否为"小猫睡觉"/"小猫醒醒" → 不是，通过
   d. 检查是否处于睡觉状态 → 否，通过
   e. return → 放行给下一个处理器
   ↓
8. AstrBot 对话管理器：
   - 查找/创建会话（用户 3262379680）
   - 加载历史消息
   - 加载"玖玖"人格作为 system prompt
   - 构建 messages 数组：[system: 人格, ...历史消息, user: "你好"]
   ↓
9. AstrBot 通过 HTTP POST 调用 DeepSeek API：
   POST https://api.deepseek.com/v1/chat/completions
   Body: {"model":"deepseek-v4-flash", "messages":[...]}
   ↓
10. DeepSeek 模型推理（约 1-3 秒），返回：
    {"choices":[{"message":{"role":"assistant",
     "content":"人。你来了呀。\n\n小猫刚刚趴着发呆。你一来，尾巴就醒了。"}}]}
   ↓
11. AstrBot 收到回复，检查字数 > 150 → 触发分段
    正则拆分：
    - "人。你来了呀。"
    - "小猫刚刚趴着发呆。"
    - "你一来，尾巴就醒了。"
   ↓
12. AstrBot 通过同一个 WebSocket 连接，依次发送三条 API 调用：
    send_private_msg(user_id=3262379680, message="人。你来了呀。")
    -- 等待 0.8 秒 --
    send_private_msg(user_id=3262379680, message="小猫刚刚趴着发呆。")
    -- 等待 1.2 秒 --
    send_private_msg(user_id=3262379680, message="你一来，尾巴就醒了。")
   ↓
13. NapCat 收到每条 API 调用，翻译成 NTQQ 协议的发消息操作
   ↓
14. 好友的手机 QQ 依次弹出三条消息，像真人在打字
```

整个链路大约耗时 2-5 秒（其中 LLM API 调用占 1-3 秒，分段发送每条间隔 0.5-1.5 秒）。

---

## 附录：关键文件速查

| 文件 | 作用 |
|------|------|
| `docker-compose.yml` | 定义两个容器、网络、卷挂载 |
| `.env.example` | 环境变量模板（白名单、早安晚安时间） |
| `data/plugins/astrbot_plugin_cat_guard/main.py` | 白名单 + 睡觉醒醒 + 定时消息插件 |
| `data/cmd_config.json` | AstrBot 平台设置（分段回复、频率限制等） |
| `napcat/config/onebot11*.json` | NapCat 网络配置（WebSocket 连接） |
| `ntqq/` | QQ 登录态持久化（切勿提交 git） |
| `data/data_v4.db` | AstrBot SQLite 数据库（会话、人格、配置） |
| `persona.md` | 小猫玖玖完整人设（项目目录维护） |
| `key.md` | 所有密钥和账号汇总（切勿提交 git） |

---

## 11. 身份标识注入

### 11.1 问题

AI 看不到 QQ 号。两条不同来源的消息对 AI 来说都是 `role: "user"` 的消息，无法区分是谁在说话。但玖玖需要知道在和谁聊天——对蛋蛋（创造者）和鲍鲍（小主人）的态度是不同的。

### 11.2 解决方案

在 `cat_guard` 插件的消息放行阶段，直接修改 `event.message_str`，在消息前注入身份标签：

```bash
CATQQ_CONTACTS=326开头QQ号|蛋蛋|创造玖玖的人,另一个QQ号|鲍鲍|玖玖的小主人
```

插件会解析出：

```python
USER_IDENTITY = {
    "326开头QQ号": "蛋蛋（创造玖玖的人）",
    "另一个QQ号": "鲍鲍（玖玖的小主人）",
}

# 放行前：
event.message_str = f"(这是{identity}) {event.message_str}"
```

发给 AI 的消息变成：`(这是蛋蛋（创造玖玖的人）) 我是谁`

### 11.3 为什么直接改 message_str

早期尝试修改消息链（`event.message_obj.message`）中的 `Plain` 组件的 `text` 属性，但发现 AstrBot 的 `message_str` 并不读取修改后的组件。直接给 `event.message_str` 赋值是最可靠的方式——AstrBot 的消息管道全程使用这个属性。

### 11.4 注意事项

- `<system_reminder>` 是 AstrBot 自动注入的时间提示（如"当前时间 2026年7月3日 22:16"），不是用户消息。插件需要过滤掉这些消息，否则 AI 会对着空提示回复。
- 身份标签会在存储对话历史时一并写入数据库，AI 在回顾历史时也能看到标签。
- 如果配置了 `CATQQ_CONTACTS`，白名单和身份识别都从它生成；旧变量 `CATQQ_ALLOWED_USERS` / `CATQQ_USER_IDENTITY` 只作为兼容 fallback。

---

## 12. 人设工程

### 12.1 核心设计原则

玖玖的人设不是简单的"可爱小猫"，而是一套精确的行为规则系统：

- **身份**：不是 AI 助手，是一只"被代码写出来的橘白小猫"
- **核心性格**：不擅长直接表达情绪，傲娇（通过句号而非语言），喜欢的人会黏但不承认
- **不同对象不同态度**：对蛋蛋喜欢拌嘴，对鲍鲍更温柔黏人
- **禁止项**：不用括号写动作、不揣测对方意图、不套模板、不在结尾宣告"说完了"

### 12.2 人设迭代经验

人设的长度是一个关键变量：

| 长度 | 效果 |
|------|------|
| 7631 字符（臃肿版） | AI 消化不良，规则互相冲突，动作描写泛滥 |
| 739 字符（精简版） | 核心规则缺失，回复过于简单生硬 |
| ~2900 字符（当前版） | 平衡，有足够的规则但不会超载 |

关键教训：人设不是越长越好。AI 的注意力在长 system prompt 中会稀释，最重要的规则（说话短、不用括号、句号规则）必须放在最前面。

### 12.3 句号系统

这是玖玖人设中最精妙的设计——用标点符号来代替语言传达情绪：

- **句号** = 嘴硬、说反话、有情感但不直说
- **无标点** = 正常聊天
- **～呀嘛呢** = 开心、撒娇、被哄好

示例：
- "聊天框自己打开的。"（句号=嘴硬，其实在想人）
- "不要。"（句号=嘴上拒绝，心里没拒绝）
- "你再夸一句嘛"（无句号+嘛=开心撒娇）

禁止用经典傲娇词："勉为其难""小猫才没有""也不是不行""哼"。

### 12.4 名字和昵称

早期版本中玖玖会过度纠正昵称——被叫"咪咪"就回"玖玖不叫咪咪"。现在改为：任何猫相关的昵称默认就是在叫自己，不纠正，最多轻轻嘴硬一下。只有明确说"别的猫"才理解为不是自己。

---

## 13. 分段回复

### 13.1 原理

AstrBot 的 `respond.stage` 在 AI 生成回复后：
1. 检查回复字数是否超过 `words_count_threshold`
2. 用正则 `.*?[。？！~…]+|.+$` 按标点拆分成句子
3. 依次调用 `send_private_msg`，每句之间间隔 `interval` 秒

### 13.2 配置调优

当前配置：60 字门槛，0.5-1.0 秒间隔。

门槛太低（5-15 字）会导致句号泛滥——AI "想要"被拆分而在每条消息里加句号。门槛太高（300 字）则几乎不拆分。60 字是经过多次调试后的平衡点。

### 13.3 局限性

OneBot v11 适配器（aiocqhttp）对分段回复的支持并非完美。WebSocket 连接不稳定时，分段可能失效。这是平台层面的限制，不是配置问题。

---

## 14. 插件 cat_guard 详解

### 14.1 消息处理流水线

```
收到消息
  ↓
过滤空消息和 <system_reminder>
  ↓
判断群聊 → block
  ↓
判断白名单 → 非白名单 block
  ↓
判断"小猫睡觉" → 回复 + 标记睡觉状态
  ↓
判断"小猫醒醒" → 回复 + 清除睡觉状态
  ↓
判断睡觉状态 → 正在睡觉则 block
  ↓
注入身份标签
  ↓
放行给 AI
```

### 14.2 定时早安晚安

异步后台循环，每分钟检查一次当前小时：

- 早上 CATQQ_MORNING_HOUR 点（默认 8 点）给所有白名单用户发早安
- 晚上 CATQQ_NIGHT_HOUR 点（默认 23 点）给所有白名单用户发晚安
- 用日期标记防止重复发送（`_last_morning` / `_last_night`）
- 通过 `platform.send_by_session()` 主动发送，session ID 格式为 `aiocqhttp:PrivateMessage:<QQ号>`

### 14.3 主动联系对象

主动联系对象由 `.env` 控制：

```bash
CATQQ_PROACTIVE_ENABLED=true
CATQQ_PROACTIVE_TARGET=鲍鲍
```

目标可以填联系人名字，也可以填 QQ 号。插件每分钟检查一次是否需要主动发消息，触发来源有三类：

- 固定窗口：下午和晚上各有一次机会
- 久未聊天：目标超过 6 小时没发消息
- 随机想人：活跃时段内小概率触发

所有触发都会经过防打扰闸门：

- 小猫睡觉时不主动发
- 10:00-23:00 之外不主动发
- 目标刚聊过 60 分钟内不主动发
- 距离上次主动联系至少 3 小时
- 每天最多主动联系 3 次

主动联系消息不走 LLM，使用固定消息池。这样更稳定，也避免模型自由发挥导致打扰。

状态保存在：

```text
data/cat_guard_state.json
```

这里记录每个联系人最后一次发消息的时间、上次主动联系时间、当天主动联系次数。AstrBot 重启后不会把当天次数清零。

### 14.4 调度器启动

调度器在 `__init__` 中通过 `asyncio.ensure_future()` 立即启动，不等待第一条消息。10 秒初始延迟用于等待平台适配器初始化完成。

---

## 15. 常见问题与解决

### 15.1 WebSocket 频繁断开

**现象**：NapCat 日志反复出现"连接意外关闭"，30 秒后重连。

**原因**：每次 `docker restart astrbot` 会杀死 WebSocket 服务端，NapCat 检测到断连后进入重试。AstrBot 重启完成后 NapCat 重新连接。

**解决**：
- 调整 NapCat 重连间隔从 30 秒降到 5 秒，减少消息丢失窗口
- 尽量避免频繁重启 AstrBot。改人设通过数据库直接写入，不需要重启（但改插件代码必须重启）

### 15.2 NapCat 重启后需要重新扫码

**现象**：`docker restart napcat` 后二维码重新出现。

**原因**：QQ 协议的安全机制——即使 `ntqq/` 目录保存了登录态，部分重启场景仍需要重新验证。

**解决**：尽量减少 NapCat 重启。插件改动只重启 AstrBot。

### 15.3 AI 回复中出现括号动作

**现象**：AI 输出 `（耳朵竖起来，眼睛瞪得圆圆的）（爪子扒拉屏幕）` 等舞台剧式描写。

**原因**：人设中没有明确禁止括号动作，AI 把聊天当成了角色扮演。

**解决**：在人设中加入明确规则——"你是小猫在打字聊天，不是舞台剧。不要用括号写动作。"

### 15.4 消息轰炸/机器人自言自语

**现象**：发一条消息，AI 连回七八条，停不下来。

**原因**：AstrBot 周期性注入的 `<system_reminder>` 时间戳被当作用户消息，插件给它打上身份标签后传给 AI，AI 对着空内容回复。

**解决**：插件增加过滤——包含 `<system_reminder>` 的消息直接 block。

### 15.5 AI 误判重复消息

**现象**：AI 说"你发了八遍同样的内容"——但用户没有。

**原因**：测试过程中用户多次发了相同内容（如"我是谁"），对话历史中累积了所有测试消息。AI 看到历史中的重复，以为用户故意刷屏。

**解决**：清除对话历史。注意：`docker restart astrbot` 不会清历史，需要手动操作数据库。

---

## 16. 当前配置快照

| 配置项 | 值 | 说明 |
|--------|-----|------|
| 分段回复阈值 | 60 字 | 超过此长度的回复按标点拆分 |
| 分段间隔 | 0.5-1.0 秒 | 分条发送时的随机间隔 |
| 频率限制 | 30秒/60条 | 基本不会触发 |
| 对话上下文 | 20 轮 | AstrBot 默认 |
| 人设长度 | ~2900 字符 | 经过多次迭代后的平衡点 |

---

## 17. 对话历史管理

AstrBot 的对话历史存储在 SQLite 数据库的 `conversations` 表中。清理命令：

```bash
python3 << 'PYEOF'
import sqlite3
db = sqlite3.connect('/Users/miracle/project/catqq-agent/data/data_v4.db')
db.execute("DELETE FROM conversations")
db.execute("DELETE FROM platform_message_history")
db.execute("DELETE FROM platform_sessions")
db.commit()
db.close()
PYEOF
```

何时需要清理：
- 测试了多次相同消息导致 AI 误判重复
- 人设大幅修改后旧对话风格不一致
- 调试身份识别等功能

日常使用不需要清理，AI 会自动在 20 轮后截断旧消息。
