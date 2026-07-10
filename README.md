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
| M5 | 智能校验：语义/事实判定 + 污染类型 + 四级风险 + 一致性 + 多裁判投票 + 多渠道告警 | ✅ |
| M6 | 企业级：可视化看板 + 人工复核后台 + 自助加模型 + 对抗检测 + 审计溯源 + 性能 | ✅ |

## 企业级升级（M6）

- **可视化看板**（Chart.js）：整体合规正确率、各模型高危趋势、风险问题 TOP 排行、污染类型分布（负面/竞品动态）。
- **人工复核后台** `/review`：审核机器判定、标注正确风险级（回流纠偏裁判）、标记疑难、就地修正标准答案 —— 数据反哺迭代。
- **自助扩展模型** `/models`：页面增删任意 OpenAI 兼容端点（通用/行业垂类/企业私域客服），无需改代码。
- **对抗性检测** `/adversarial`：内置诱导/极限/污染类攻击模板，对某产品一键对抗巡检，考察各 AI 抗污染能力。
- **权限/审计/溯源**：admin/reviewer/viewer 三级角色；`/audit` 操作日志（写操作全记录）；判定保存标准答案/裁判/投票快照可溯源；CSV 批量导出。
- **性能**：题库/查询分页、看板查询限量、关键索引，支撑大批量题库与高频巡检。

## 智能校验（M5）

从"字面匹配"升级为语义级智能校验：

- **语义 + 事实判定**：裁判以标准答案为产品权威口径，按语义判断正确性，识别"字面相近但语义歪曲 / 逻辑错误 / 信息缺失 / 编造"。
- **恶意污染识别**：分类 `false_info`(虚假信息)/`defamation`(负面抹黑)/`rumor`(不实谣言)/`exaggeration`(违规夸大)。
- **四级风险**：正常 / 轻微偏差 / 中度偏移 / 严重污染错误。
- **口径一致性**：手动批次可设「一致性采样次数」>1，同题多次问答对比，识别不稳定/前后矛盾。
- **多裁判投票**：每条答案判定 `JUDGE_VOTES` 次(默认3)取多数、平票取更重，降误判/漏判；正确性过低自动兜底至中度以上。
- **人工标记回流**：查询页可标注正确风险级，回流为 few-shot 纠偏裁判；仪表盘显示判定准确率。
- **多渠道告警**：中高危(risk≥`ALERT_RISK_THRESHOLD`)或口径矛盾时，推送 **企业微信 / 邮件 / 飞书**（各自按 env 配置开关）。

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
