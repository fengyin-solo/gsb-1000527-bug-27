"""地质勘探数据管理平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.services.survey_point import service as survey_service
from app.store import store

app = FastAPI(title="地质勘探数据管理平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)


@app.on_event("startup")
def build_initial_aggregate() -> None:
    """启动即完成首次聚合重算（含存量缺责任组迁移），三处视图拿到同一口径。"""
    survey_service.rebuild("startup-rebuild")


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：各业务模块待处理量汇总成看板卡片，并挂测绘控制统一聚合快照。"""
    payload = store.overview()
    payload["survey_control"] = survey_service.get_snapshot()
    return payload
