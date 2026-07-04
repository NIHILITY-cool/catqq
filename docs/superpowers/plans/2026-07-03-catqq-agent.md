# CatQQ Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a QQ cat chatbot ("玖玖") using NapCatQQ + AstrBot + Docker Compose + DeepSeek V4 Flash.

**Architecture:** Five files total — `.gitignore`, `.env.example`, `docker-compose.yml`, `metadata.yaml`, and `main.py` (the plugin handling whitelist guard, sleep/wake, and scheduled greetings). The rest is Docker runtime and AstrBot WebUI configuration.

**Tech Stack:** Docker Compose, NapCatQQ (mlikiowa/napcat-docker), AstrBot (soulter/astrbot), Python 3 (AstrBot plugin API), DeepSeek V4 Flash API

## Global Constraints

- QQ小号: 3757296370 (login via NapCat WebUI QR scan, never in files)
- Whitelist: read from `CATQQ_ALLOWED_USERS` env var, default `3262379680,1906310787`
- Sleep trigger: "小猫睡觉" → reply "咪睡了，人晚安"
- Wake trigger: "小猫醒醒" → cat-style reply
- Morning greeting: 08:00 daily (hour configurable via `CATQQ_MORNING_HOUR`)
- Night greeting: 23:00 daily (hour configurable via `CATQQ_NIGHT_HOUR`)
- Cat name: 玖玖 (known to the persona, not forced into every reply)
- Model: `deepseek-v4-flash` via OpenAI-compatible endpoint `https://api.deepseek.com`
- Ports: 6099 (NapCat), 6185 (AstrBot), both bound to 127.0.0.1 only
- API Key: NEVER in files — configured via AstrBot WebUI

---

### Task 1: Project scaffolding — `.gitignore` and `.env.example`

**Files:**
- Create: `.gitignore`
- Create: `.env.example`

**Interfaces:**
- Produces: `.env.example` template with `CATQQ_ALLOWED_USERS`, `CATQQ_MORNING_HOUR`, `CATQQ_NIGHT_HOUR` variables that `main.py` reads via `os.environ.get()`

- [ ] **Step 1: Create `.gitignore`**

```bash
cat > .gitignore << 'EOF'
# QQ login session — NEVER commit
ntqq/

# NapCat auto-generated config
napcat/config/

# AstrBot runtime data (except plugins)
data/log/
data/data.db
data/config/

# User .env with personal settings
.env
EOF
```

- [ ] **Step 2: Create `.env.example`**

```bash
cat > .env.example << 'EOF'
# 白名单 QQ 号，逗号分隔
CATQQ_ALLOWED_USERS=3262379680,1906310787

# 早安时间（24小时制，默认8点）
CATQQ_MORNING_HOUR=8

# 晚安时间（24小时制，默认23点）
CATQQ_NIGHT_HOUR=23
EOF
```

- [ ] **Step 3: Copy to `.env` for local use**

```bash
cp .env.example .env
```

- [ ] **Step 4: Verify files exist**

```bash
ls -la .gitignore .env.example .env
```

Expected: all three files present, `.gitignore` includes `ntqq/` and `.env`

---

### Task 2: Docker Compose configuration

**Files:**
- Create: `docker-compose.yml`

**Interfaces:**
- Produces: Two Docker services (`napcat`, `astrbot`) on internal bridge network, sharing `./data:/AstrBot/data`

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
services:
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    restart: always
    environment:
      - NAPCAT_UID=${NAPCAT_UID:-1000}
      - NAPCAT_GID=${NAPCAT_GID:-1000}
      - MODE=astrbot
    ports:
      - "127.0.0.1:6099:6099"
    volumes:
      - ./data:/AstrBot/data
      - ./napcat/config:/app/napcat/config
      - ./ntqq:/app/.config/QQ
    networks:
      - astrbot_network

  astrbot:
    image: soulter/astrbot:latest
    container_name: astrbot
    restart: always
    environment:
      - TZ=Asia/Shanghai
      - CATQQ_ALLOWED_USERS=${CATQQ_ALLOWED_USERS:-3262379680,1906310787}
      - CATQQ_MORNING_HOUR=${CATQQ_MORNING_HOUR:-8}
      - CATQQ_NIGHT_HOUR=${CATQQ_NIGHT_HOUR:-23}
    ports:
      - "127.0.0.1:6185:6185"
    volumes:
      - ./data:/AstrBot/data
    networks:
      - astrbot_network

networks:
  astrbot_network:
    driver: bridge
```

- [ ] **Step 2: Pull Docker images**

```bash
docker compose pull
```

Expected: both `mlikiowa/napcat-docker:latest` and `soulter/astrbot:latest` pulled successfully.

---

### Task 3: Plugin metadata

**Files:**
- Create: `data/plugins/astrbot_plugin_cat_guard/metadata.yaml`

**Interfaces:**
- Produces: Plugin named `astrbot_plugin_cat_guard` registered for the `aiocqhttp` platform

- [ ] **Step 1: Create plugin directory**

```bash
mkdir -p data/plugins/astrbot_plugin_cat_guard
```

- [ ] **Step 2: Create `metadata.yaml`**

```yaml
name: astrbot_plugin_cat_guard
display_name: 小猫白名单守卫
desc: 白名单守卫 + 小猫睡觉/醒醒 + 早安晚安定时消息
version: 1.0.0
author: catqq
repo: none
support_platforms:
  - aiocqhttp
```

- [ ] **Step 3: Verify**

```bash
ls -la data/plugins/astrbot_plugin_cat_guard/metadata.yaml
```

Expected: file exists.

---

### Task 4: Plugin main.py — configuration, whitelist guard, and sleep/wake

**Files:**
- Create: `data/plugins/astrbot_plugin_cat_guard/main.py`

**Interfaces:**
- Consumes: `CATQQ_ALLOWED_USERS`, `CATQQ_MORNING_HOUR`, `CATQQ_NIGHT_HOUR` from env vars
- Produces: `Main` class extending `Star` with `cat_guard` event handler; blocks non-whitelist/group/sleeping messages; handles sleep/wake words

- [ ] **Step 1: Write the whitelist guard + sleep/wake core**

```python
"""CatQQ Agent — 小猫白名单守卫插件.

Whitelist guard + sleep/wake + scheduled greetings.
"""
import os
import asyncio
import random
from datetime import datetime, date

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

# ---------------------------------------------------------------------------
# Configuration (from environment variables, with defaults)
# ---------------------------------------------------------------------------

ALLOWED_USERS: set[str] = set(
    uid.strip()
    for uid in os.environ.get("CATQQ_ALLOWED_USERS", "3262379680,1906310787").split(",")
    if uid.strip()
)

MORNING_HOUR: int = int(os.environ.get("CATQQ_MORNING_HOUR", "8"))
NIGHT_HOUR: int = int(os.environ.get("CATQQ_NIGHT_HOUR", "23"))

SLEEP_WORD: str = "小猫睡觉"
WAKE_WORD: str = "小猫醒醒"
SLEEP_REPLY: str = "咪睡了，人晚安"

# Morning / night message pools (cat-persona style)
MORNING_MESSAGES: list[str] = [
    "人，早。小猫看着你醒的。",
    "早呀。小猫已经醒了，一直在等你。",
    "人，新的一天。小猫的尾巴先和你说早安。",
    "醒了呀人。小猫等你很久了。",
]

NIGHT_MESSAGES: list[str] = [
    "人，该睡了。小猫已经困成猫饼了。",
    "很晚了人。小猫把爪子揣好，等你一起睡。",
    "该睡觉了。小猫不熬夜，你也不许熬夜。",
    "人，晚安。小猫就趴在你旁边。",
]


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class Main(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.sleeping: bool = False
        self._last_morning: date | None = None
        self._last_night: date | None = None
        self._scheduler_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _start_scheduler(self) -> None:
        """Background loop: check time every 60 s, fire greetings on the hour."""
        await asyncio.sleep(10)  # Let platform adapters initialise
        logger.info("[cat_guard] scheduler started")
        while True:
            try:
                now = datetime.now()
                today = now.date()

                if now.hour == MORNING_HOUR and self._last_morning != today:
                    self._last_morning = today
                    await self._send_greetings("morning")

                if now.hour == NIGHT_HOUR and self._last_night != today:
                    self._last_night = today
                    await self._send_greetings("night")

            except Exception as exc:
                logger.error(f"[cat_guard] scheduler error: {exc}")

            await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Scheduled greetings
    # ------------------------------------------------------------------

    async def _send_greetings(self, greeting_type: str) -> None:
        """Send a greeting message to every whitelisted user."""
        if greeting_type == "morning":
            msg = random.choice(MORNING_MESSAGES)
        else:
            msg = random.choice(NIGHT_MESSAGES)

        # Try to get a platform adapter that can send proactive messages.
        platform = self._get_platform()
        if platform is None:
            logger.warning("[cat_guard] no platform available for greetings")
            return

        for user_id in ALLOWED_USERS:
            try:
                # UMO session-id format: platform:message_type:session_id
                # Verify with `/sid` in AstrBot if this does not work.
                session_id = f"aiocqhttp:PrivateMessage:{user_id}"
                await platform.send_by_session(session_id, msg)
                logger.info(
                    f"[cat_guard] sent {greeting_type} greeting to {user_id}"
                )
                await asyncio.sleep(0.5)  # Small gap to avoid rate-limiting
            except Exception as exc:
                logger.error(
                    f"[cat_guard] failed to send {greeting_type} to {user_id}: {exc}"
                )

    def _get_platform(self):
        """Return the first available OneBot v11 platform adapter.

        Walks ``context.platform_manager.platform_insts`` if available,
        otherwise falls back to ``context.platform``.
        """
        # Attempt 1: platform_manager (astrbot.api convention)
        pm = getattr(self.context, "platform_manager", None)
        if pm is not None:
            insts = getattr(pm, "platform_insts", [])
            if insts:
                return insts[0]

        # Attempt 2: direct platform client (astrbot_sdk convention)
        platform = getattr(self.context, "platform", None)
        if platform is not None and hasattr(platform, "send"):
            return platform

        return None

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def cat_guard(self, event: AstrMessageEvent):
        """Whitelist guard + sleep/wake gate for every incoming message."""

        # Lazy-start the scheduler on first message (ensures loop is running).
        if self._scheduler_task is None:
            self._scheduler_task = asyncio.ensure_future(self._start_scheduler())

        user_id = str(event.get_sender_id())
        message = (event.message_str or "").strip()

        # --- Block all group messages ---
        group_id = getattr(event.message_obj, "group_id", "")
        if group_id:
            logger.info(
                f"[cat_guard] block group message: group={group_id} user={user_id}"
            )
            event.stop_event()
            return

        # --- Block non-whitelist users ---
        if user_id not in ALLOWED_USERS:
            logger.info(f"[cat_guard] block non-allowed user: {user_id}")
            event.stop_event()
            return

        # --- Sleep word ---
        if message == SLEEP_WORD:
            self.sleeping = True
            event.stop_event()
            yield event.plain_result(SLEEP_REPLY)
            return

        # --- Wake word ---
        if message == WAKE_WORD:
            self.sleeping = False
            event.stop_event()
            # Cat-style wake reply — use AI via plain_result or a static pool.
            wake_replies = [
                "咪醒了。人，早上好呀。",
                "小猫醒了。人，你叫我？",
                "嗯……小猫醒了。尾巴先醒的。",
            ]
            yield event.plain_result(random.choice(wake_replies))
            return

        # --- Sleeping → block ---
        if self.sleeping:
            logger.info(f"[cat_guard] sleeping, ignore message from {user_id}")
            event.stop_event()
            return

        # --- Pass through to LLM ---
        return

    async def terminate(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
        logger.info("[cat_guard] terminated")
```

- [ ] **Step 2: Verify file structure**

```bash
ls -la data/plugins/astrbot_plugin_cat_guard/
```

Expected: both `metadata.yaml` and `main.py` present.

- [ ] **Step 3: Verify Python syntax (optional, if Python available)**

```bash
python3 -c "import ast; ast.parse(open('data/plugins/astrbot_plugin_cat_guard/main.py').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

---

### Task 5: Start services

**Files:** None (uses Docker Compose)

- [ ] **Step 1: Start containers**

```bash
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d
```

Expected: both containers start, no errors.

- [ ] **Step 2: Check container status**

```bash
docker ps --filter "name=napcat" --filter "name=astrbot" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Expected: both `napcat` and `astrbot` show `Up`.

- [ ] **Step 3: Check NapCat logs for WebUI URL**

```bash
docker logs napcat 2>&1 | tail -20
```

Expected: see WebUI URL or login QR information.

- [ ] **Step 4: Check AstrBot logs for startup**

```bash
docker logs astrbot 2>&1 | tail -20
```

Expected: AstrBot started, no fatal errors, plugin loaded (look for `[cat_guard]` or plugin registration messages).

---

### Task 6: Configure AstrBot (persona, model, OneBot bot)

**Files:** None (WebUI configuration)

- [ ] **Step 1: Open AstrBot WebUI**

```text
http://127.0.0.1:6185
```

- [ ] **Step 2: Create OneBot v11 bot**

Navigate: 左侧 Bots / 机器人 → Create Bot / 创建机器人

Fill in:
| Field | Value |
|-------|-------|
| ID | catqq |
| Platform | OneBot v11 |
| Enable | On |
| Reverse WebSocket Host | 0.0.0.0 |
| Reverse WebSocket Port | 6199 |
| Reverse WebSocket Path | /ws |
| Token | (leave empty) |

Save.

- [ ] **Step 3: Add DeepSeek provider**

Navigate: Providers / 服务提供商 → Add / 添加

Fill in:
| Field | Value |
|-------|-------|
| Type | OpenAI Compatible |
| Name | DeepSeek V4 Flash |
| API Base URL | https://api.deepseek.com |
| API Key | 你的DeepSeek-API-Key |
| Model | deepseek-v4-flash |

Save.

- [ ] **Step 4: Set default model**

Navigate: Config / 配置 → Default Chat Model → select "DeepSeek V4 Flash" → Save.

- [ ] **Step 5: Create cat persona**

Navigate: Persona / 人格 → Create

Paste the complete cat persona prompt (玖玖, the tsundere cat) as the persona content. Name it "小猫-玖玖". Set as default.

---

### Task 7: Connect NapCat to AstrBot

**Files:** None (WebUI configuration)

- [ ] **Step 1: Open NapCat WebUI**

```text
http://127.0.0.1:6099/webui
```

- [ ] **Step 2: Login with QQ小号**

Scan the QR code with QQ account 3757296370.

Expected: NapCat shows logged-in status.

- [ ] **Step 3: Add WebSocket client**

Navigate: 网络配置 → 新建 → WebSocket客户端

Fill in:
| Field | Value |
|-------|-------|
| 名称 | AstrBot |
| 启用 | On |
| URL | ws://astrbot:6199/ws |
| Token | (leave empty) |

Save.

- [ ] **Step 4: Restart NapCat**

```bash
docker restart napcat
```

- [ ] **Step 5: Verify connection**

Check AstrBot logs:
```bash
docker logs astrbot 2>&1 | grep -i "aiocqhttp\|connected\|adapter" | tail -5
```

Expected: `aiocqhttp(OneBot v11) adapter connected` or similar success message.

---

### Task 8: End-to-end verification

**Files:** None (manual testing via QQ)

- [ ] **Step 1: Whitelist user sends "你好"**

From QQ 3262379680 to 3757296370: send "你好"

Expected: Cat-style reply from 玖玖.

- [ ] **Step 2: Non-whitelist user sends "你好"**

From any QQ NOT in the whitelist to 3757296370: send "你好"

Expected: No reply.

- [ ] **Step 3: Group @mention**

In any group containing 3757296370: @ the bot.

Expected: No reply.

- [ ] **Step 4: Sleep — "小猫睡觉"**

Whitelist user sends: 小猫睡觉

Expected: Reply "咪睡了，人晚安", then all subsequent messages are ignored.

- [ ] **Step 5: Wake — "小猫醒醒"**

Whitelist user sends: 小猫醒醒

Expected: Cat-style wake reply, normal chat resumes.

- [ ] **Step 6: Verify plugin logs**

```bash
docker logs astrbot 2>&1 | grep "\[cat_guard\]" | tail -20
```

Expected: see guard decisions logged (block/allow/sleep/wake events).

