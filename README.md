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
│           ├── main.py       ← 插件入口（白名单/身份/定时）
│           └── proactive.py  ← 主动联系对象逻辑
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
- 主动找人：可配置一个联系人，久未聊天/固定时段/随机想人时主动私聊
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

换主动联系对象：编辑 `.env` 里的 `CATQQ_CONTACTS` 和 `CATQQ_PROACTIVE_TARGET`。

加功能：改 `data/plugins/astrbot_plugin_cat_guard/main.py` 或同目录下的辅助模块。

详见 **[PRINCIPLE.md](PRINCIPLE.md)**。
