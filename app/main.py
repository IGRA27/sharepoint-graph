from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers.sharepoint import router as sharepoint_router

app = FastAPI(title="SharePoint IO", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.ALLOW_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sharepoint_router)

@app.get("/")
def root():
    return {"service": "sharepoint-io", "status": "ok"}
