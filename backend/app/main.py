"""地质勘探数据管理平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
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


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。

    测绘控制一行直接取聚合服务的同源快照，保证运营概览与测绘看板、
    控制点台账、点位图工作台口径一致。
    """
    data = store.overview()
    try:
        from app.routers.survey_point import service as survey_service

        snapshot = survey_service.ensure_snapshot()
    except Exception:  # 聚合不可用时退回 store 口径，不拖垮总览
        return data
    stats = snapshot["stats"]
    for module in data["modules"]:
        if module["name"] == "survey_point":
            module["created"] = int(stats["控制点总数"])
            module["pending"] = int(stats["待复核"])
            module["abnormal"] = int(stats["按点位状态"].get("损坏", 0))
            module["已复核"] = int(stats["已复核"])
            module["图幅数"] = int(stats["图幅数"])
            break
    # 卡片重算（其他模块不变，只替换测绘控制贡献的三个数字）
    data["cards"] = [
        {"label": "业务模块", "value": len(data["modules"])},
        {"label": "今日新增", "value": sum(int(item["created"]) for item in data["modules"])},
        {"label": "待处理", "value": sum(int(item["pending"]) for item in data["modules"])},
        {"label": "异常量", "value": sum(int(item["abnormal"]) for item in data["modules"])},
    ]
    return data
