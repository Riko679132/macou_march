#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
api_server.py —— 澳门绿色通关与碳足迹评估系统 · FastAPI 服务
（原 app.py 改名而来；app.py 现为 Streamlit 演示页）

启动（本地）：
    python -m uvicorn api_server:app --host 127.0.0.1 --port 8000 --reload

接口：
    POST /api/analyze          body: {"item_desc": "...", "direction": "内地入境澳门"}
    GET  /api/analyze?item_desc=...&direction=...   （浏览器地址栏可直接测试）
    GET  /health               健康检查

安全约定（为微信小程序/云端做准备）：
    - API Key 只从环境变量 DEEPSEEK_API_KEY 读取（agent_brain.load_config 已优先 env），
      本文件与仓库里绝不出现密钥；
    - 小程序端请求时 Key 留在服务器，前端只传 item_desc / direction；
    - 上线时把 127.0.0.1 换成 0.0.0.0，并加 HTTPS + 鉴权（如 API Token）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import agent_brain as ab

app = FastAPI(
    title="澳门绿色通关与碳足迹评估 API",
    description="输入携带物品描述与通关方向，返回红黄绿裁决 + 生命周期碳足迹。",
    version="0.2.0",
)

# 开发期放开跨域；上线后收紧为小程序/前端正式域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    item_desc: str = Field(..., min_length=1, max_length=500,
                           description="携带物品描述，如：我带了2公斤含肉松的饼干和1条香烟")
    direction: Optional[str] = Field(
        default="内地入境澳门",
        description="通关方向：内地入境澳门 / 澳门入境内地（也接受 inbound_macau 等枚举）")


class AnalyzeResponse(BaseModel):
    status: str
    reason: str
    identified_items: list = []
    carbon_footprint: Optional[float] = None
    carbon_unit: str = "kg CO2e"
    suggestion: Optional[str] = None
    disclaimer: str


@app.post("/api/analyze", response_model=AnalyzeResponse,
          summary="智能通关裁决（POST）")
def analyze(req: AnalyzeRequest) -> dict:
    """调用 agent_brain.analyze_item；内部已做降级，不会抛异常。"""
    return ab.analyze_item(req.item_desc, req.direction)


@app.get("/api/analyze", response_model=AnalyzeResponse,
         summary="智能通关裁决（GET，便于浏览器直接测试）")
def analyze_get(
    item_desc: str = Query(..., min_length=1, max_length=500,
                           description="携带物品描述"),
    direction: str = Query("内地入境澳门", description="通关方向"),
) -> dict:
    return ab.analyze_item(item_desc, direction)


@app.get("/health", summary="健康检查")
def health() -> dict:
    cfg = ab.load_config()
    key, source = ab.resolve_api_key(cfg)
    return {
        "app": "macau_eco_travel",
        "version": "0.2.0",
        "llm_model": cfg["deepseek"]["model"],
        "api_key_source": source,          # env / config.yaml / none（不返回密钥本身）
        "api_key_ready": bool(key),
        "db": cfg["database"]["path"],
    }


@app.get("/", summary="浏览器友好首页")
def index() -> dict:
    return {
        "message": "澳门绿色通关与碳足迹评估 API",
        "post": {"url": "/api/analyze",
                 "body": {"item_desc": "我带了2公斤含肉松的饼干和1条香烟",
                          "direction": "内地入境澳门"}},
        "get_demo": "/api/analyze?item_desc=%E6%88%91%E5%B8%A6%E4%BA%862%E5%85%AC%E6%96%A4%E5%90%AB%E8%82%89%E6%9D%BE%E7%9A%84%E9%A5%BC%E5%B9%B2&direction=%E5%86%85%E5%9C%B0%E5%85%A5%E5%A2%83%E6%BE%B3%E9%97%A8",
        "health": "/health",
    }
