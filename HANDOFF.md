# CatQQ Agent 交付指南

> 给接手这只需要被照顾的电子小猫的人，最好是在你计算机基础不是太差的情况下阅读。

## 第一步：安装 Docker Desktop

先去 [docker.com](https://www.docker.com/products/docker-desktop/) 下载安装 Docker Desktop。

安装完打开，菜单栏出现鲸鱼图标就说明在运行了。不用登录 Docker Hub 账号，跳过就行。

## 第二步：下载项目

打开终端（Mac 按 `Cmd+Space` 搜"终端"），逐行执行：

```bash
# 克隆项目代码
git clone git@github.com:NIHILITY-cool/catqq.git
cd catqq-agent
```

如果 git clone 不行（提示权限错误），就用 HTTPS 方式：

```bash
git clone https://github.com/NIHILITY-cool/catqq.git
cd catqq-agent
```

## 第三步：解压数据包

把 `catqq-agent-full.tar.gz` 放到 `catqq-agent` 目录里，然后：

```bash
tar -xzvf catqq-agent-full.tar.gz
```

解压后 `data/`、`ntqq/`、`napcat/config/`、`.env` 会出现在目录下。用 Finder 看一下确认 `docker-compose.yml` 和 `.env` 都在同一个目录里。

## 第四步：启动容器

```bash
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d
```

等着拉镜像，第一次可能要几分钟。完成后：

```bash
docker ps
```

看到 `napcat` 和 `astrbot` 两个名字都是 `Up` 状态就对了。

## 第五步：扫码登录 QQ

```bash
docker logs napcat 2>&1 | grep "WebUi Token"
```

会输出类似这样的内容：

```
[NapCat] [WebUi] WebUi Token: d5289498cc20
[NapCat] [WebUi] WebUi User Panel Url: http://127.0.0.1:6099/webui?token=d5289498cc20
```

复制日志里那个 `http://127.0.0.1:6099/webui?token=xxxx` 的完整链接，浏览器打开。

用 QQ 小号扫码。扫完后页面会显示登录成功。

> ⚠️ 此处请注意：另一个人那边的 bot 先关掉，否则两边同时登录同一个 QQ 号会被踢。

## 第六步：改 AstrBot 后台密码

打开 `http://127.0.0.1:6185`。

查登录密码：

```bash
docker logs astrbot 2>&1 | grep "Initial password"
```

用 `astrbot` 和日志里那个密码登录。进去后改掉密码。

## 第七步：确认一切正常

```bash
docker logs astrbot 2>&1 | grep "适配器已连接"
```

看到 `aiocqhttp(OneBot v11) 适配器已连接` 说明连通了。

用白名单里的 QQ 给机器人发一条消息，收到回复就大功告成。

---

## 日常使用

**启动**（Mac 开机后 Docker Desktop 自动启动的话，执行这个）：

```bash
cd catqq-agent
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d
```

**关闭**：

```bash
cd catqq-agent
docker compose down
```

**查看机器人说了什么**：

```bash
docker logs -f astrbot
```

按 `Ctrl+C` 退出。

---

## 常见问题

**重启后 QQ 掉线**

QQ 有时会要求重新扫码。打开 NapCat WebUI（第五步的地址）重新扫一下就行。Token 每次可能不一样，用第五步的方式重新查。

**机器人不回消息**

```bash
docker logs astrbot --tail 20
```

看看有没有报错。通常重启一下就好：

```bash
docker restart astrbot
```

**容器启动报错**

Docker Desktop 没在运行。打开 Docker Desktop 等鲸鱼图标停止转动。

---

## 自定义

**改人设**：编辑 `persona.md`，然后运行：

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

**改白名单**：编辑 `.env`，修改 `CATQQ_ALLOWED_USERS` 和 `CATQQ_USER_IDENTITY` 的值，然后：

```bash
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d astrbot
```
