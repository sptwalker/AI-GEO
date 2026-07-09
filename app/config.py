"""应用配置：统一从环境变量 / .env 读取。

密钥等敏感信息只走环境变量、不入库（见 database 设计）。各模型的 API Key
由 adapters/registry 按 `llm_model.api_key_env` 指定的变量名读取，因此这里
不逐个声明模型密钥字段。
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- 应用 ----
    app_secret: str = "change-me-in-prod"   # 会话 Cookie 签名密钥
    admin_username: str = "admin"
    admin_password: str = "admin123"        # MVP：明文比对；生产请改强口令或前置反代鉴权
    run_scheduler: bool = False             # M2 定时调度开关

    # ---- 数据库 ----
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "ai_geo"
    # 可选：直接给完整连接串，覆盖上面的分项（部署/本地 sqlite 测试用）
    database_url_override: str | None = Field(default=None, validation_alias="DATABASE_URL")

    # ---- 判定 ----
    judge_model_key: str = "deepseek"       # 判定裁判模型（须在 llm_model 启用且配好密钥）
    correctness_threshold: int = 60         # 正确性低于此分计入报警

    # ---- 采集并发 ----
    ask_concurrency: int = 6                # ponytail: 全局并发上限，够用；按模型限流二期再拆
    ask_timeout: float = 60.0
    ask_max_retries: int = 2

    # ---- 定时调度（M2）----
    scheduler_timezone: str = "Asia/Shanghai"

    # ---- 飞书报警（M2）----
    alert_webhook_url: str | None = None    # 飞书自定义群机器人 Webhook
    alert_webhook_secret: str | None = None  # 可选：机器人"加签"密钥

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
