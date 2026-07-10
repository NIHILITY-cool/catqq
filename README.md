# CatQQ Agent

通过 QQ 账号聊天的 AI 小猫，基于 NapCatQQ + AstrBot + Docker Compose。

```
QQ消息 → NapCatQQ → OneBot v11 → AstrBot → DeepSeek → 小猫回复
```

## 快速开始

```bash
git clone git@github.com:NIHILITY-cool/catqq.git
cd catqq-agent
cp .env.example .env          # 编辑 .env，填自己的信息
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d
```

然后：
1. 打开 NapCat WebUI 扫码登录 QQ 小号
2. 打开 AstrBot WebUI 配置 AI 模型和人设
3. 用白名单 QQ 发消息，小猫就会回你

详见 **[START.md](START.md)**。

## 项目结构

```
catqq-agent/
├── README.md                 ← 你在这里
├── HANDOFF.md                ← 完整交付指南
├── START.md                  ← 启动与维护指南
├── PRINCIPLE.md              ← 技术原理详解
├── persona.md                ← 小猫人设（可替换）
├── docker-compose.yml        ← 容器编排
├── .env.example              ← 环境变量模板
├── .gitignore                ← 排除敏感文件
│
├── data/                     ← AstrBot 数据（git 跟踪）
│   └── plugins/
│       └── astrbot_plugin_cat_guard/
│           ├── metadata.yaml ← 插件元信息
│           ├── main.py       ← 插件入口（白名单/身份/定时/工具拦截）
│           └── proactive.py  ← 主动联系和任务工具纯逻辑
│
├── data/                     ← AstrBot 数据（git 忽略，运行时生成）
│   ├── data_v4.db            ← SQLite 数据库（对话/人设/配置）
│   ├── cmd_config.json       ← 平台配置（分段回复等）
│   └── ...
│
├── napcat/config/            ← NapCat 配置（git 忽略，自动生成）
├── ntqq/                     ← QQ 登录态（git 忽略，切勿上传）
│
├── docs/superpowers/         ← 设计文档
│   ├── specs/                ← 需求规格
│   └── plans/                ← 实现计划
│
└── catqq-agent-full.tar.gz   ← 完整打包（git 忽略，发给别人用）
```

> ⚠️ `data/data_v4.db` `ntqq/` `napcat/config/` `.env` `key.md` 都不会上传 GitHub。别人 clone 后需要自己配置，或者从你发的压缩包里解压。

## 特性

- 白名单守卫：只回复指定好友
- 身份识别：AI 知道在和谁聊天，不同人不同态度
- 睡觉/醒醒：可暂停和恢复回复
- 早安晚安：定时主动发消息
- 主动联系：小猫可按防打扰规则主动私聊一个固定联系人
- 小猫任务工具：自然对话里让小猫带话、询问、提醒、安排未来任务、查看/取消任务；LLM 先按人设判断，再由插件执行隐藏工具指令
- 工具防绕路：启动后和消息进入 LLM 前都会停用会拿错 session 的内置跨会话工具，避免出现"找不到蛋蛋/鲍鲍"这类误判
- 分段回复：长回复自动拆分，模拟真人聊天
- 句号情绪系统：通过标点传情绪，不靠傲娇词汇

## 技术栈

- [NapCatQQ](https://github.com/NapNeko/NapCat-Docker) — QQ 协议适配
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 机器人框架
- [DeepSeek V4](https://api-docs.deepseek.com/) — AI 模型（可替换为任意 OpenAI 兼容 API）
- Docker Compose — 容器编排

## 自定义

换人设：编辑 `persona.md`，运行同步脚本。

换 AI：AstrBot WebUI 里添加任意 OpenAI 兼容提供商。

换联系人：编辑 `.env` 里的 `CATQQ_CONTACTS`。

换主动联系对象：编辑 `.env` 里的 `CATQQ_PROACTIVE_TARGET`。

让小猫执行任务：自然说 `你现在去给鲍鲍说考试加油`、`下午三点问问鲍鲍考完了吗`、`小猫看看你还记着什么任务`。不要再使用旧的 `小猫任务：...` 固定入口；是否执行、怎么措辞由玖玖按人设判断。

加功能：改 `data/plugins/astrbot_plugin_cat_guard/main.py` 或同目录下的辅助模块。

详见 **[PRINCIPLE.md](PRINCIPLE.md)**。
