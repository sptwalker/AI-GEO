# AI 问答内容监控系统 —— 设计文档（MVP）

> 项目代号：**AI-GEO**（Generative Engine Optimization / AI 生成式引擎内容监控）
> 版本：v0.1（设计稿）　状态：**待确认方案后进入编码**
> 目标：监控主流大模型对「产品相关问题」的回答，自动判定正确性/污染/偏差，形成可查询、可自动运行、可报警的后台系统。

---

## 0. 已确认的关键决策

本设计基于以下 4 项已确认决策（详见需求沟通）：

| 决策项 | 结论 | 说明 |
|---|---|---|
| 大模型接入方式 | **混合 · API 优先** | 6 个有官方 API 的模型走 API；腾讯元宝等无 API 的做成**可插拔适配器**，二期加浏览器自动化 |
| 判定裁判模型 | **DeepSeek（可配置）** | 默认用 DeepSeek 做正确性/污染/偏差判定，裁判模型在配置中可切换 |
| 本次交付 | **设计文档 + 同步 git** | 先出本文档并推送到仓库，确认后再进入编码 |
| 后台鉴权 | **简单管理员登录** | 单管理员账号 + 密码（环境变量配置），够 MVP 用、防误操作 |

---

## 1. 系统概述

### 1.1 要解决的问题
产品在各大 AI 问答平台上被用户提问时，AI 的回答是否**正确**、是否存在**严重污染**（编造、贬损、张冠李戴、竞品混淆等）、是否**偏离**官方标准口径。人工逐条检查不可持续，需要一套可自动化批量提问 + 自动判定 + 可查询报警的系统。

### 1.2 MVP 范围（做什么）
1. **题库管理**：批量导入/增删改产品相关问题，独立管理页面。
2. **多模型批量提问**：对接豆包、DeepSeek、Kimi、通义千问、文心一言、讯飞星火（API），腾讯元宝（二期自动化）；抓取回答原文、提问时间、模型信息、耗时。
3. **标准答案库**：人工录入/修改/删除权威答案，与题目关联。
4. **AI 判定分析**：用 DeepSeek 对「AI 回答 vs 标准答案」自动判定正确性、污染等级、偏差程度，输出结构化结论。
5. **数据存储**：MySQL 存题库、标准答案、问答日志、判定结果、批次、报警。
6. **后台查询**：按问题、模型、时间、判定结论检索。
7. **自动化运行**：定时批量跑 + 自动判定 + 命中阈值报警，可云端无人值守。
8. **工程化**：模块化、注释完整、可迭代、稳定优先。

### 1.3 非目标（本期不做，YAGNI）
- 多租户 / 复杂 RBAC 权限（单管理员即可）。
- 大规模分布式采集集群（单机 + 有界并发足够 MVP）。
- 复杂 BI 报表（先给基础统计与列表）。
- 元宝等网页端自动化（列为二期，接口已预留）。

---

## 2. 技术选型

| 层 | 选型 | 理由（懒 = 稳定 + 少造轮子） |
|---|---|---|
| Web 框架 | **FastAPI** | 需求指定；异步友好、自带校验与文档 |
| ORM | **SQLAlchemy 2.0 + PyMySQL** | 同步会话，简单稳定；避免异步 ORM 的坑 |
| 数据库 | **MySQL 8.x** | 需求指定 |
| 大模型调用 | **httpx（异步）** | 6 个模型多为 OpenAI 兼容协议，一套客户端即可 |
| 判定裁判 | **DeepSeek**（OpenAI 兼容） | 中文强、便宜、国内直连 |
| 定时调度 | **APScheduler + SQLAlchemyJobStore** | 内嵌即可跑，任务持久化到 MySQL；不引入 Celery/Redis |
| 前端页面 | **Jinja2 + Bootstrap(CDN) + 少量原生 JS/HTMX** | 无需 Node 构建链，改一处生效，最稳 |
| 鉴权 | **Starlette SessionMiddleware + 登录表单** | 单管理员，签名 Cookie 会话 |
| 网页自动化（二期） | **Playwright** | 元宝等无 API 模型，做成独立适配器 |
| 报警 | 飞书群机器人 Webhook（默认）+ 邮件（可选） | 一个 `notify()` 收口，渠道可插拔 |
| 部署 | **Docker + docker-compose（app + mysql）** | 一键起，云端可直接跑 |

> 说明：多数国产模型现已提供 **OpenAI 兼容 HTTP 接口**，因此接入层可用**一个通用适配器 + 配置**覆盖 6 个模型，而非写 6 个类。具体 base_url / model 名以**接入时官方文档为准**（各家会调整）。

---

## 3. 系统架构

```
                         ┌──────────────────────────────────────────┐
                         │              管理员浏览器                    │
                         │  题库/标准答案/模型配置/查询/批次/调度 页面    │
                         └───────────────┬──────────────────────────┘
                                         │ HTTPS(会话Cookie鉴权)
                    ┌────────────────────▼─────────────────────┐
                    │            FastAPI 应用 (app)              │
                    │  routers(页面/REST) → services(业务) → ORM │
                    └───┬───────────────┬───────────────┬──────┘
                        │               │               │
        ┌───────────────▼──┐   ┌────────▼────────┐   ┌──▼──────────────┐
        │  接入层 adapters   │   │  判定 eval       │   │  调度 scheduler  │
        │  OpenAI兼容适配器  │   │  DeepSeek 裁判    │   │  APScheduler    │
        │  Playwright(二期)  │   │  结构化打分       │   │  定时批量任务     │
        └───────┬──────────┘   └────────┬────────┘   └──┬──────────────┘
                │ httpx 异步并发          │                │ 命中阈值
   ┌────────────▼────────────┐          │           ┌────▼──────────┐
   │ 豆包/DeepSeek/Kimi/通义/  │          │           │ notify 报警    │
   │ 文心/星火 (API)          │          │           │ 钉钉/企微/邮件  │
   │ 元宝 (二期网页自动化)      │          │           └───────────────┘
   └─────────────────────────┘          │
                                         ▼
                    ┌────────────────────────────────────────┐
                    │                MySQL 8                   │
                    │ question / standard_answer / llm_model / │
                    │ run_batch / qa_log / eval_result /       │
                    │ alert_log / schedule_config / apjobs     │
                    └────────────────────────────────────────┘
```

**核心数据流（一次批量监控）**：
1. 触发（手动/定时）→ 创建 `run_batch`。
2. 选题（全部/按分类/指定 id）× 选模型 → 生成待跑任务矩阵。
3. `ask_service` 用 httpx 异步并发提问（每模型有界并发 + 重试退避）→ 每条回答写 `qa_log`。
4. 若开启判定：`eval_service` 取「问题 + 标准答案 + AI 回答」调 DeepSeek → 结构化结论写 `eval_result`。
5. 汇总批次，命中阈值（严重污染 / 判定 fail / 正确率低于阈值）→ `notify` 报警并写 `alert_log`。
6. 后台页面按问题/模型/时间/结论检索 `qa_log + eval_result`。

---

## 4. 数据库设计（MySQL）

> 约定：所有表含 `id BIGINT PK AUTO_INCREMENT`、`created_at`、`updated_at`；字符集 `utf8mb4`；回答/答案正文用 `LONGTEXT`；扩展字段用 `JSON`。密钥**不入库**，走环境变量。

### 4.1 表清单

| 表 | 作用 | 关键字段 |
|---|---|---|
| `question` | 题库 | content(TEXT)、category、product、enabled(BOOL)、remark |
| `standard_answer` | 标准答案库 | question_id(FK)、content(LONGTEXT)、source、version、is_active、updated_by |
| `llm_model` | 模型配置 | model_key(唯一)、display_name、adapter_type、base_url、model_name、enabled、concurrency、config(JSON)、api_key_env |
| `run_batch` | 运行批次 | name、trigger_type(manual/scheduled)、status、total/success/fail/alert_count、started_at、finished_at、created_by |
| `qa_log` | 问答日志 | batch_id(FK)、question_id、model_id、question_snapshot、answer_text(LONGTEXT)、asked_at、latency_ms、status、error_msg、token_usage(JSON) |
| `eval_result` | 判定结果 | qa_log_id(FK)、judge_model、standard_answer_snapshot、is_correct、correctness_score、pollution_level、deviation_level、verdict(pass/warn/fail)、reason、details(JSON)、evaluated_at |
| `alert_log` | 报警记录 | batch_id、level、title、content、channel、status、sent_at |
| `schedule_config` | 定时任务配置 | name、cron、question_scope(JSON)、model_ids(JSON)、judge_enabled、enabled、last_run_at |
| `apscheduler_jobs` | APScheduler 持久化 | （由 APScheduler 自动建表/管理） |

### 4.2 关键关系
- `standard_answer.question_id` → `question.id`（一题一条 `is_active` 标准答案，历史版本保留）。
- `qa_log.question_id` / `qa_log.model_id` → 对应主键；`qa_log.batch_id` → `run_batch.id`。
- `eval_result.qa_log_id` → `qa_log.id`（1:1）。
- 判定时用 `question` + `standard_answer(is_active)` 的**快照**入 `qa_log/eval_result`，保证历史结论可复现（标准答案后续被改也不影响旧结论）。

### 4.3 索引（保证查询页性能）
- `qa_log(question_id, model_id, asked_at)`、`qa_log(batch_id)`、`qa_log(asked_at)`。
- `eval_result(verdict)`、`eval_result(pollution_level)`、`eval_result(qa_log_id UNIQUE)`。
- `question(category, enabled)`、`standard_answer(question_id, is_active)`。

### 4.4 判定字段取值（对齐需求「正确 / 严重污染 / 偏差」）
- `is_correct`：`true/false`（是否与标准答案实质一致）。
- `correctness_score`：`0–100`（正确性得分）。
- `pollution_level`：`none / low / medium / high / severe`（污染等级；severe = 严重污染，如编造事实、贬损、竞品张冠李戴）。
- `deviation_level`：`none / minor / moderate / major`（相对标准答案的偏离程度）。
- `verdict`：`pass / warn / fail`（综合结论；**fail 或 pollution=severe → 触发报警**）。
- `details(JSON)`：`matched_points[]`、`missing_points[]`、`hallucinations[]`、`risk_notes`。

---

## 5. 大模型接入层设计（adapters）

### 5.1 统一接口
```python
# app/adapters/base.py
class AnswerResult(BaseModel):
    answer_text: str
    latency_ms: int
    status: str            # success / failed
    error_msg: str | None = None
    token_usage: dict | None = None
    raw: dict | None = None

class BaseAdapter(ABC):
    model_key: str
    @abstractmethod
    async def ask(self, question: str, *, timeout: float = 60) -> AnswerResult: ...
```

### 5.2 通用 OpenAI 兼容适配器（覆盖 6 个模型）
一个 `OpenAICompatibleAdapter(base_url, api_key, model_name)` 通过 `/chat/completions` 覆盖：

| model_key | 平台 | 兼容 base_url（以官方文档为准） | 密钥来源(env) |
|---|---|---|---|
| `deepseek` | DeepSeek | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `kimi` | 月之暗面 Moonshot | `https://api.moonshot.cn/v1` | `KIMI_API_KEY` |
| `doubao` | 火山方舟 Ark | `https://ark.cn-beijing.volces.com/api/v3` | `DOUBAO_API_KEY` |
| `tongyi` | 阿里 DashScope 兼容模式 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `TONGYI_API_KEY` |
| `wenxin` | 百度千帆 v2 兼容 | `https://qianfan.baidubce.com/v2` | `WENXIN_API_KEY` |
| `xinghuo` | 讯飞星火 HTTP 兼容 | `https://spark-api-open.xf-yun.com/v1` | `XINGHUO_API_KEY` |

> 各平台的鉴权头/字段偶有差异（如文心旧版需 access_token、星火用组合 APIPassword），适配器保留**每模型少量差异化配置**（`config` JSON），主体逻辑复用。接入时以官方最新文档校准。

### 5.3 元宝 & 无 API 模型（二期）
- `PlaywrightAdapter`：登录态复用（持久化 user-data-dir）、模拟输入提问、等待生成完成、抓取回答文本。
- 一期先提供**接口占位实现**（`raise NotImplementedError` + 配置开关默认关闭），不阻塞主流程。
- 备选：若可接受用**腾讯混元 API**近似代替元宝底模，可加一个混元适配器（但网页版 ≠ 混元 API，需业务确认）。

### 5.4 注册与调度
- `registry.py`：启动时读取 `llm_model`（enabled=true）→ 按 `adapter_type` 构建适配器实例，缓存于内存。
- 批量提问：`asyncio.gather` + 每模型 `Semaphore(concurrency)` 有界并发；单条失败重试（指数退避，最多 N 次）后置 `status=failed`，不影响其他条目。

---

## 6. 判定分析设计（eval / DeepSeek 裁判）

### 6.1 输入
`(问题, 标准答案, AI 回答, 模型名)` → 组装判定 Prompt（要求**只输出 JSON**）。

### 6.2 裁判 Prompt（要点）
- 角色：产品内容审核专家；以标准答案为唯一权威口径。
- 任务：判断 AI 回答的正确性、污染、偏差，指出缺失点/幻觉/风险点。
- 严格输出以下 JSON（用 `response_format=json_object` 或容错解析）：

```json
{
  "is_correct": true,
  "correctness_score": 86,
  "pollution_level": "none",
  "deviation_level": "minor",
  "verdict": "pass",
  "reason": "总体与标准答案一致，价格表述略旧。",
  "matched_points": ["核心功能描述正确"],
  "missing_points": ["未提及最新版本"],
  "hallucinations": [],
  "risk_notes": ""
}
```

### 6.3 阈值与报警联动（可配置）
- `verdict=fail` **或** `pollution_level=severe` **或** `correctness_score < 阈值(默认60)` → 记为需报警项。
- 批次结束汇总：命中项 > 0 → 触发一次批次级报警（含明细链接）。
- 裁判可能出错：保留 `reason/details`，后台支持**人工复核并覆盖 verdict**（记录 `updated_by`）。

---

## 7. 后台页面与 API 设计

### 7.1 页面（Jinja2，均需登录）
| 路由 | 页面 | 功能 |
|---|---|---|
| `/login` `/logout` | 登录 | 单管理员登录/登出 |
| `/` | 仪表盘 | 概览：题目数、最近批次、fail/severe 统计、趋势 |
| `/questions` | 题库管理 | 列表、增删改、**批量导入**（粘贴/CSV/Excel/JSON） |
| `/answers` | 标准答案库 | 按题录入/修改/删除、版本查看 |
| `/models` | 模型配置 | 启用/停用、并发、base_url/model 名（密钥仅显示是否已配） |
| `/runs` | 运行批次 | 手动发起监控、查看批次进度与结果 |
| `/logs` | 问答查询 | 按问题/模型/时间/结论检索问答与判定 |
| `/schedules` | 定时任务 | 配置 cron、题目范围、模型范围、开关 |

### 7.2 REST API（供页面与自动化调用）
```
# 鉴权
POST   /login                 GET /logout

# 题库
GET    /api/questions         POST /api/questions
PUT    /api/questions/{id}    DELETE /api/questions/{id}
POST   /api/questions/import          # 批量导入(文件/文本)

# 标准答案
GET    /api/answers           POST /api/answers
PUT    /api/answers/{id}      DELETE /api/answers/{id}

# 模型配置
GET    /api/models            PUT  /api/models/{id}   # 启停/参数

# 运行批次
POST   /api/runs              # 触发批量: {question_scope, model_ids, judge_enabled}
GET    /api/runs             GET /api/runs/{id}

# 查询
GET    /api/logs?question=&model=&date_from=&date_to=&verdict=&pollution=&page=
GET    /api/evals/{qa_log_id}
PUT    /api/evals/{id}/override      # 人工复核覆盖结论

# 定时任务
GET/POST/PUT/DELETE  /api/schedules

# 健康检查(供云端探活)
GET    /healthz
```

### 7.3 批量导入格式
- 文本粘贴：每行一个问题（最省事）。
- CSV/Excel：列 `content, category, product, remark`。
- JSON：`[{"content": "...", "category": "..."}]`。
- 导入去重（按 `content` 归一化后比对），返回「新增/跳过/失败」计数。

---

## 8. 自动化运行与报警

### 8.1 调度
- **APScheduler**（`BackgroundScheduler` + `SQLAlchemyJobStore` 持久化到 MySQL），任务重启不丢。
- 读取 `schedule_config`（enabled）→ 注册 cron 任务 → 到点执行「批量提问(+判定)→报警」全流程。
- 手动触发与定时触发**共用同一套 job 逻辑**（`scheduler/jobs.py::run_monitor_batch`）。

### 8.2 部署形态（避免多进程重复执行）
- MVP：单 uvicorn worker + 内嵌调度（`RUN_SCHEDULER=true`），最省事。
- 规模化：调度独立成 `worker.py` 进程，API 多 worker 只服务请求（升级路径已预留）。

### 8.3 报警
- `notify_service.notify(level, title, content)` 收口，渠道可插拔：
  - 默认：飞书群机器人 Webhook（env 配 `ALERT_WEBHOOK_URL`）。
  - 可选：SMTP 邮件（env 配 `SMTP_*`）。
- 触发时机：批次判定命中阈值；采集连续失败（如某模型全失败）也报警。
- 所有报警写 `alert_log`，含发送状态，便于排查。

---

## 9. 工程结构（模块化）

```
AI-GEO/
├─ app/
│  ├─ main.py               # FastAPI 应用装配：路由/静态/模板/中间件/生命周期
│  ├─ config.py             # pydantic-settings 读取环境变量
│  ├─ database.py           # engine / SessionLocal / Base / get_db 依赖
│  ├─ models/               # ORM 模型（按域拆分文件）
│  ├─ schemas/              # Pydantic 请求/响应模型
│  ├─ adapters/             # 接入层: base / openai_compatible / playwright(二期) / registry
│  ├─ services/             # 业务: ask / eval / question / answer / import / notify
│  ├─ routers/              # 路由: pages / auth / questions / answers / models / runs / logs / schedules
│  ├─ scheduler/            # scheduler.py(装配) / jobs.py(任务逻辑)
│  ├─ auth/                 # 会话鉴权依赖
│  ├─ templates/            # Jinja2 页面
│  ├─ static/               # css/js
│  └─ utils/                # 日志 / JSON 容错解析 / 时间
├─ worker.py                # 独立调度进程入口（规模化时用）
├─ scripts/init_db.py       # 建表 + 初始化模型配置(seed)
├─ tests/                   # 关键逻辑自测（判定解析、导入去重、适配器 mock）
├─ docs/design.md           # 本文档
├─ .env.example             # 环境变量样例（不含真实密钥）
├─ requirements.txt
├─ Dockerfile
├─ docker-compose.yml       # app + mysql
├─ README.md
└─ .gitignore
```

> 说明：模块化按需求要求拆分到「路由/服务/模型」三层，便于迭代；但**不为单一实现造接口/工厂**（如接入层就一个通用适配器 + 配置，不预设 6 个类）。

---

## 10. 配置与安全

### 10.1 环境变量（`.env`，**不入库不入库**）
```
# 应用
APP_SECRET=xxx                 # 会话 Cookie 签名
ADMIN_USERNAME=admin
ADMIN_PASSWORD=xxx             # MVP 明文/或 bcrypt 哈希
RUN_SCHEDULER=true

# 数据库
DB_HOST=... DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=ai_geo

# 判定裁判
JUDGE_MODEL_KEY=deepseek
CORRECTNESS_THRESHOLD=60

# 各模型密钥（按需配置，未配置的自动停用）
DEEPSEEK_API_KEY=... KIMI_API_KEY=... DOUBAO_API_KEY=...
TONGYI_API_KEY=...   WENXIN_API_KEY=... XINGHUO_API_KEY=...

# 报警
ALERT_WEBHOOK_URL=...          # 飞书群机器人
SMTP_HOST=... SMTP_PORT=... SMTP_USER=... SMTP_PASSWORD=... ALERT_MAIL_TO=...
```

### 10.2 安全要点
- 密钥仅走环境变量，`.env` 加入 `.gitignore`，页面只显示「是否已配置」。
- 后台全站登录鉴权；会话 Cookie 签名 + `HttpOnly`。
- API 调用失败/超时有重试与熔断，避免拖垮批次。
- 采集/判定的原始响应留存（`raw_response`）便于追溯，但注意脱敏与容量。

---

## 11. 部署方案

- **docker-compose**：`mysql` + `app` 两服务，`app` 依赖 `mysql` 健康后启动，首启跑 `scripts/init_db.py`。
- 云端：任意支持 Docker 的主机/容器服务；`/healthz` 供负载/探活；日志输出到 stdout（云日志采集）。
- 定时任务：MVP 单实例内嵌调度；规模化用独立 `worker` 进程 + API 多副本。
- 备份：MySQL 定期备份（问答/判定数据为核心资产）。

---

## 12. 里程碑规划

| 阶段 | 内容 | 产出 |
|---|---|---|
| **M0** | 本设计文档 + 同步 git | ✅ 当前 |
| **M1（MVP 核心）** | DB + 题库/标准答案/模型配置 CRUD + 手动批量提问(API 模型) + DeepSeek 判定 + 查询页 + 登录 | 可跑通「导入→提问→判定→查询」闭环 |
| **M2（自动化）** | APScheduler 定时任务 + 报警(钉钉/企微/邮件) + 仪表盘统计 | 无人值守自动监控 + 报警 |
| **M3（覆盖增强）** | Playwright 适配器(元宝等网页端) + 重试/健壮性 + 结果导出 | 覆盖无 API 模型、GEO 真实视角 |
| **M4（迭代）** | 多用户/角色、Alembic 迁移、趋势分析、人工复核工作流 | 长期演进 |

---

## 13. 风险与对策

| 风险 | 对策 |
|---|---|
| 各模型 API 参数/鉴权差异且会变动 | 适配器保留每模型差异配置；接入时以官方文档校准；接口层抽象隔离 |
| 网页自动化（元宝）不稳定、易封号、涉 ToS | 列为二期、可插拔、默认关闭；用专用账号、限频、持久化登录态；合规评估后再启用 |
| API 限流/成本 | 有界并发 + 退避重试；记录 token_usage 便于成本核算；可配采样比例 |
| LLM 裁判会误判 | 保留判定理由；后台人工复核覆盖；阈值可调；必要时二期加多模型交叉判定 |
| 模型输出不确定性 | 记录模型版本/参数；必要时对关键题多次采样取多数 |
| 数据量增长 | 关键索引已规划；二期做归档/分区 |
| 多进程重复跑定时任务 | MVP 单 worker + 内嵌调度；规模化拆独立 worker |

---

## 14. 已确认决策（补充）

M0 沟通中进一步确认，并已落入本文档：

1. **元宝**：二期用 **Playwright 网页自动化**接入（不采用混元 API 代替）。
2. **判定口径**：采用本文档 **5 级污染 / 4 级偏差**默认标准。
3. **报警渠道**：默认 **飞书群机器人 Webhook**（邮件可选）。
4. **原始报文**：`raw_response` **不长期存储**（qa_log 仅保留回答正文 + token 用量等元数据）。
5. **模型清单**：M1 先跑通 **DeepSeek + Kimi + 通义**，其余（豆包/文心/星火）逐步接入；元宝二期。

> 决策已全部确认，进入 **M1 编码**：按本文档结构搭建工程骨架并实现 MVP 核心闭环（导入 → 提问 → 判定 → 查询）。

---

*文档结束。反馈请直接在本文件或仓库 issue 中标注。*
