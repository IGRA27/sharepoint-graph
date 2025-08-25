from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional, List, Dict

from fastapi import APIRouter, HTTPException, UploadFile, File, Body, Query
from fastapi.responses import StreamingResponse, JSONResponse

from app.config import settings
from app.services.sharepoint_client import SharePointClient

router = APIRouter(prefix="/sharepoint", tags=["sharepoint"])

SPANISH_MONTHS = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
    5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}
def month_folder_name(n: int) -> str:
    return f"{n}. {SPANISH_MONTHS[n]}"


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/config-check")
def config_check():
    return {
        "SITE_HOSTNAME": settings.SITE_HOSTNAME,
        "SITE_PATH": settings.SITE_PATH,
        "TIMEZONE": settings.TIMEZONE,
        "HAS_AAD_TENANT_ID": bool(settings.AAD_TENANT_ID),
        "HAS_AAD_CLIENT_ID": bool(settings.AAD_CLIENT_ID),
        "HAS_AAD_CLIENT_SECRET": bool(settings.AAD_CLIENT_SECRET),
    }


@router.post("/download", summary="Descarga (stream) un archivo por path o item_id")
def sp_download(
    path: Optional[str] = Body(None),
    item_id: Optional[str] = Body(None),
):
    try:
        client = SharePointClient()
        stream = client.stream_file(path=path, item_id=item_id)
        _, name = client.get_download_url(path=path, item_id=item_id)
        headers = {"Content-Disposition": f'attachment; filename="{name}"'}
        return StreamingResponse(stream, headers=headers)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error al descargar: {exc}") from exc


@router.post("/upload", summary="Sube un archivo a una carpeta de SharePoint")
async def sp_upload(
    file: UploadFile = File(...),
    target_path: str = Query("", description="Carpeta destino (ej: 'Documentos compartidos/Resultados')"),
    filename: Optional[str] = Query(None, description="Nombre final opcional"),
):
    try:
        data = await file.read()
        fname = filename or file.filename or "upload.bin"
        client = SharePointClient()
        meta = client.upload(io.BytesIO(data), target_path=target_path, filename=fname)
        return {
            "id": meta.get("id"),
            "name": meta.get("name"),
            "webUrl": meta.get("webUrl"),
            "size": meta.get("size"),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error al subir: {exc}") from exc


@router.post("/resolve-arribo", summary="Devuelve path del ARRIBO según Año → 'N. MES'")
def resolve_arribo(
    base_path: str = Body(..., description="Ej: 'Documentos compartidos/SKU/Nuevos productos CME'"),
    year: Optional[int] = Body(None),
    month: Optional[int] = Body(None),
    arribo_name_contains: str = Body("ARRIBO"),
    arribo_extensions: List[str] = Body([".xlsm", ".xlsx"]),
):
    """
    Estructura asumida:
      <base_path>/<AÑO>/<N. MES>/*.xlsm|.xlsx
    Retorna el archivo más reciente que cumpla el filtro (o cualquier Excel si no hay 'ARRIBO' en nombre).
    """
    try:
        client = SharePointClient()
        if not year or not month:
            now = datetime.now(ZoneInfo(settings.TIMEZONE))
            year = year or now.year
            month = month or now.month

        month_dir = month_folder_name(int(month))
        arribo_folder = f"{base_path.strip('/')}/{year}/{month_dir}"

        candidates = client.find_in_folder(
            arribo_folder, name_contains=arribo_name_contains, extensions=arribo_extensions, is_file=True
        )
        if not candidates:
            candidates = client.find_in_folder(
                arribo_folder, name_contains=None, extensions=arribo_extensions, is_file=True
            )
        if not candidates:
            raise HTTPException(status_code=404, detail=f"No hay Excel en {arribo_folder}")

        candidates.sort(key=lambda it: it.get("lastModifiedDateTime", ""), reverse=True)
        it = candidates[0]
        return {
            "folder": arribo_folder,
            "name": it["name"],
            "path": f"{arribo_folder}/{it['name']}",
            "lastModifiedDateTime": it.get("lastModifiedDateTime"),
            "size": it.get("size"),
            "id": it.get("id"),
            "webUrl": it.get("webUrl"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error resolviendo ARRIBO: {exc}") from exc
