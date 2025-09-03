# app/services/sharepoint_client.py
from __future__ import annotations

import io
import os
import requests
from functools import lru_cache
from typing import Optional, Tuple, List, Dict
from urllib.parse import quote

from msal import ConfidentialClientApplication
from msal.oauth2cli.http import HttpClient, Response  #versión MSAL que expone HttpClient/Response

from app.config import settings

CHUNK_BASE = 320 * 1024
CHUNK_SIZE = 5 * CHUNK_BASE  # ~1.6MiB * 5 ≈ 8MiB


class _RequestsHttpClient(HttpClient):
    """HttpClient para MSAL que respeta verify y timeout."""
    def __init__(self, verify: bool = True, timeout: int = 30):
        self._s = requests.Session()
        self._verify = verify
        self._timeout = timeout

    def get(self, url, headers=None, **kwargs):
        r = self._s.get(
            url, headers=headers, timeout=self._timeout,
            verify=self._verify, allow_redirects=True
        )
        resp = Response()  # sin args en algunas versiones
        resp.status_code = r.status_code
        resp.headers = r.headers
        resp.text = r.text
        return resp

    def post(self, url, headers=None, data=None, **kwargs):
        r = self._s.post(
            url, headers=headers, data=data, timeout=self._timeout,
            verify=self._verify, allow_redirects=True
        )
        resp = Response()  # sin args en algunas versiones
        resp.status_code = r.status_code
        resp.headers = r.headers
        resp.text = r.text
        return resp


class SharePointClient:
    def __init__(self) -> None:
        self._ensure_config()
        self._verify = bool(getattr(settings, "SSL_VERIFY", True))
        self._http_client = _RequestsHttpClient(verify=self._verify)

        self._app = ConfidentialClientApplication(
            settings.AAD_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{settings.AAD_TENANT_ID}",
            client_credential=settings.AAD_CLIENT_SECRET,
            http_client=self._http_client,  # MSAL usa nuestro client (respeta verify)
        )

    @staticmethod
    def _ensure_config():
        missing = [k for k in ("AAD_TENANT_ID", "AAD_CLIENT_ID", "AAD_CLIENT_SECRET")
                   if not getattr(settings, k)]
        if missing:
            raise RuntimeError(
                "Faltan variables de entorno para SharePoint/MS Graph: "
                + ", ".join(missing)
                + ". Define estas en tu .env o docker-compose."
            )

    def _token(self) -> str:
        token = self._app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in token:
            raise RuntimeError(token.get("error_description", "No se pudo obtener el token AAD"))
        return token["access_token"]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    @lru_cache(maxsize=1)
    def _site_id(self) -> str:
        url = f"https://graph.microsoft.com/v1.0/sites/{settings.SITE_HOSTNAME}:{settings.SITE_PATH}"
        resp = requests.get(url, headers=self._headers(), timeout=30, verify=self._verify)
        resp.raise_for_status()
        return resp.json()["id"]

    @lru_cache(maxsize=1)
    def _drive_id(self) -> str:
        url = f"https://graph.microsoft.com/v1.0/sites/{self._site_id()}/drive"
        resp = requests.get(url, headers=self._headers(), timeout=30, verify=self._verify)
        resp.raise_for_status()
        return resp.json()["id"]

    @staticmethod
    def _encode_path(path: str) -> str:
        return quote((path or "").strip("/"), safe="/")

    @staticmethod
    def _normalize_path(path: str) -> str:
        """
        Convierte rutas a 'relativas al drive root'.
        Acepta:
          - "Documentos compartidos/SKU/..."
          - "Shared Documents/SKU/..."
          - "/sites/Loyalty2021/Documentos compartidos/SKU/..."
          - "SKU/..."
        """
        p = (path or "").strip().lstrip("/")

        # Si viene con /sites/<site>/... quítalo
        site_prefix = settings.SITE_PATH.strip("/").lower() + "/"
        if p.lower().startswith(site_prefix):
            p = p[len(site_prefix):]

        # Quita el nombre de la biblioteca si viene
        for lib in ("documentos compartidos/", "shared documents/"):
            if p.lower().startswith(lib):
                p = p[len(lib):]
                break

        return p

    # ---------- Descarga ----------
    def get_item_by_path(self, path: str) -> dict:
        enc = self._encode_path(self._normalize_path(path))
        url = f"https://graph.microsoft.com/v1.0/sites/{self._site_id()}/drive/root:/{enc}"
        resp = requests.get(url, headers=self._headers(), timeout=30, verify=self._verify)
        resp.raise_for_status()
        return resp.json()

    def get_item_by_id(self, item_id: str) -> dict:
        url = f"https://graph.microsoft.com/v1.0/drives/{self._drive_id()}/items/{item_id}"
        resp = requests.get(url, headers=self._headers(), timeout=30, verify=self._verify)
        resp.raise_for_status()
        return resp.json()

    def get_download_url(self, *, path: Optional[str] = None, item_id: Optional[str] = None) -> Tuple[str, str]:
        if not path and not item_id:
            raise ValueError("Proporciona 'path' o 'item_id'")
        info = self.get_item_by_path(path) if path else self.get_item_by_id(item_id)  # type: ignore
        dl_url = info.get("@microsoft.graph.downloadUrl")
        if not dl_url:
            if path:
                enc = self._encode_path(self._normalize_path(path))
                dl_url = f"https://graph.microsoft.com/v1.0/sites/{self._site_id()}/drive/root:/{enc}:/content"
            else:
                dl_url = f"https://graph.microsoft.com/v1.0/drives/{self._drive_id()}/items/{item_id}/content"
        return dl_url, info.get("name", "download.bin")

    def stream_file(self, *, path: Optional[str] = None, item_id: Optional[str] = None):
        dl_url, _ = self.get_download_url(path=path, item_id=item_id)
        resp = requests.get(dl_url, headers=self._headers(), stream=True, timeout=60, verify=self._verify)
        resp.raise_for_status()
        return resp.iter_content(chunk_size=1024 * 256)

    # ---------- Listado/Búsqueda ----------
    def list_children(self, folder_path: str) -> List[Dict]:
        enc = self._encode_path(self._normalize_path(folder_path))
        url = f"https://graph.microsoft.com/v1.0/sites/{self._site_id()}/drive/root:/{enc}:/children"
        resp = requests.get(url, headers=self._headers(), timeout=30, verify=self._verify)
        resp.raise_for_status()
        return resp.json().get("value", [])

    def find_in_folder(
        self,
        folder_path: str,
        name_contains: Optional[str] = None,
        extensions: Optional[List[str]] = None,
        is_file: bool = True,
    ) -> List[Dict]:
        items = self.list_children(folder_path)
        out = []
        for it in items:
            is_file_item = "file" in it
            if is_file != is_file_item:
                continue
            nm = it.get("name", "")
            if name_contains and name_contains.lower() not in nm.lower():
                continue
            if extensions and is_file_item:
                if not any(nm.lower().endswith(ext.lower()) for ext in extensions):
                    continue
            out.append(it)
        return out

    # ---------- Subida ----------
    def upload(self, fileobj: io.BufferedReader | io.BytesIO, target_path: str, filename: str) -> dict:
        target_path = self._normalize_path(target_path).strip("/")
        full_path = f"{target_path}/{filename}" if target_path else filename
        enc = self._encode_path(full_path)

        fileobj.seek(0, os.SEEK_END)
        size = fileobj.tell()
        fileobj.seek(0)

        if size <= 4 * 1024 * 1024:
            url = f"https://graph.microsoft.com/v1.0/sites/{self._site_id()}/drive/root:/{enc}:/content"
            resp = requests.put(url, headers=self._headers(), data=fileobj.read(), timeout=120, verify=self._verify)
            resp.raise_for_status()
            return resp.json()

        # Sesión por chunks
        create_url = f"https://graph.microsoft.com/v1.0/sites/{self._site_id()}/drive/root:/{enc}:/createUploadSession"
        session = requests.post(create_url, headers=self._headers(), json={}, timeout=30, verify=self._verify)
        session.raise_for_status()
        upload_url = session.json()["uploadUrl"]

        bytes_sent = 0
        while True:
            chunk = fileobj.read(min(CHUNK_SIZE, size - bytes_sent))
            if not chunk:
                break
            start = bytes_sent
            end = bytes_sent + len(chunk) - 1
            headers = {"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{end}/{size}"}
            put = requests.put(upload_url, headers=headers, data=chunk, timeout=300, verify=self._verify)
            if put.status_code in (200, 201):
                return put.json()
            elif put.status_code not in (202,):
                put.raise_for_status()
            bytes_sent = end + 1

        raise RuntimeError("Sesión de upload finalizó sin respuesta de item")
