from __future__ import annotations

import os

from fastapi import HTTPException
from fastapi.responses import FileResponse


def resolve_web_file(path: str, web_dir: str) -> str | None:
    clean_path = path.lstrip("/")
    target = os.path.abspath(os.path.join(web_dir, clean_path))
    root = os.path.abspath(web_dir)
    if not target.startswith(root):
        return None
    if os.path.isfile(target):
        return target
    return None


def serve_spa(web_dir: str, path: str, api_prefixes: tuple[str, ...]):
    static_path = resolve_web_file(path, web_dir)
    if static_path:
        return FileResponse(static_path)

    if path.startswith(api_prefixes):
        raise HTTPException(status_code=404)

    index_path = resolve_web_file("index.html", web_dir)
    if index_path:
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail=f"web assets not found under {web_dir}")

