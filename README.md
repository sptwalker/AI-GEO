# AI-GEO —— AI 问答内容监控系统

监控主流大模型（豆包 / DeepSeek / Kimi / 通义千问 / 文心一言 / 讯飞星火 / 腾讯元宝）对**产品相关问题**的回答，自动判定**正确性 / 污染 / 偏差**，并支持查询、定时自动运行与报警。

- 技术栈：Python + FastAPI + MySQL + SQLAlchemy + DeepSeek（判定裁判）+ Jinja2
- 接入方式：**API 优先**（6 个模型走官方 OpenAI 兼容接口），元宝等无 API 模型二期用 Playwright 网页自动化（可插拔适配器）

📄 完整设计见 [docs/design.md](docs/design.md)

## 当前状态：M1（MVP 核心）已实现

打通闭环 **导入 → 提问 → 判定 → 查询**：题库管理、标准答案库、模型配置、手动批量提问（API 模型）、DeepSeek 判定、后台查询、管理员登录。

## 快速开始

### 方式一：docker-compose（含 MySQL，一键起）

```bash
cp .env.example .env      # 改好 ADMIN_PASSWORD、各模型 API Key
docker compose up -d      # 启动后访问 http://localhost:8000 （用管理员账号登录）
```

### 方式二：本地运行（自备 MySQL）

```bash
pip install -r requirements.txt
cp .env.example .env       # 配好数据库连接与模型密钥
python -m scripts.init_db  # 建表 + 初始化模型（应用启动也会自动执行）
uvicorn app.main:app --reload
```

启动后：`/login` 登录 → `/questions` 导入题库 → `/answers` 录标准答案 → `/models` 配置/启用模型 → `/runs` 发起监控 → `/logs` 查询结果。

> 至少配置一个模型的 API Key（默认启用 DeepSeek / Kimi / 通义），并把判定裁判 `JUDGE_MODEL_KEY`（默认 deepseek）的密钥配好，判定才会生效。

## 自测

```bash
python -m tests.test_core   # 或 pytest
```

## 目录结构

```
app/
  main.py            应用装配（中间件/路由/启动建表）
  config.py          环境变量配置
  database.py        引擎/会话
  models.py          ORM 模型（题库/答案/模型/批次/日志/判定）
  schemas.py         Pydantic 结构（含判定输出）
  bootstrap.py       建表 + 初始化默认模型
  auth.py            管理员会话鉴权
  adapters/          接入层（通用 OpenAI 兼容 + 网页自动化占位 + 注册表）
  services/          业务（ask 批量提问 / eval 判定 / import 导入）
  routers/           路由（auth/pages/questions/answers/models/runs/logs）
  templates/         Jinja2 页面
scripts/init_db.py   手动初始化数据库
tests/test_core.py   关键逻辑自测
```

## 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 设计文档 | ✅ |
| M1 | MVP 核心闭环（导入→提问→判定→查询）| ✅ |
| M2 | 定时自动运行 + 飞书报警 + 仪表盘增强 | ✅ |
| M3 | 元宝等网页端自动化（Playwright）+ CSV 导出 + 健壮性 | ✅ |
| M4 | 多用户/角色 + Alembic 迁移 + 趋势图表 | ✅ |

## 定时自动运行 + 报警（M2）

- 单进程内嵌调度：设 `RUN_SCHEDULER=true` 启动应用即自动跑 `/schedules` 里配置的 cron 任务。
- 报警走飞书自定义群机器人：配 `ALERT_WEBHOOK_URL`（可选 `ALERT_WEBHOOK_SECRET` 加签）；批次命中 `fail`/严重污染即推送汇总。
- 仪表盘含分模型判定统计与近 14 天趋势图。

## 网页自动化 + 导出（M3）

- **元宝等无 API 模型**：用 Playwright 持久化登录态。首次登录：
  ```bash
  pip install playwright && playwright install chromium
  # 先在「模型配置」页给 yuanbao 填 url 与 answer_selector 并保存
  python -m scripts.yuanbao_login          # 打开浏览器手动登录一次
  ```
  之后适配器无头复用登录态。选择器（输入框/发送/回答）均在模型配置页填写，可用卡片「测试」按钮验证连通。
- **CSV 导出**：`/logs` 按当前筛选导出、`/runs/{id}` 导出单批次，带 BOM，Excel 直接打开。

## 多用户与角色（M4）

- 用户存 DB，密码 pbkdf2 哈希；首次启动用 `ADMIN_USERNAME/ADMIN_PASSWORD` 播种初始 **admin**。
- 角色：**admin**（全权）/ **viewer**（只读：可查询/导出，不能改动或发起运行）。写操作服务端 `require_admin` 强校验，页面按角色隐藏写控件。
- `/users` 页（admin）管理用户；有"最后一个管理员/当前账号"保护，防锁死。

## 数据库迁移（M4，可选）

默认 `AUTO_CREATE_TABLES=true` 启动自动建表（开发便捷）。生产可用 Alembic 受控迁移：

```bash
alembic stamp head                              # 已有(create_all)库首次纳入迁移
alembic revision --autogenerate -m "xxx"        # 改模型后生成迁移
alembic upgrade head                            # 应用迁移（配 AUTO_CREATE_TABLES=false 时用它建表）
```

## 说明

- 各模型 `base_url` / `model_name` 以接入时官方文档为准，可在「模型配置」页调整；未配置密钥的模型即使启用也会被自动跳过。
- 按既定决策：判定裁判默认 DeepSeek；污染 5 级 / 偏差 4 级；报警走飞书（M2）；问答日志不长期存储原始报文。
