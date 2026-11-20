"""ビルド済みの画面をAPIと同じオリジンから配信する。"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import REPOSITORY_ROOT


def mount_web(api: FastAPI) -> None:
    directory = Path(os.environ.get("WORKSHOP_WEB_DIST", str(REPOSITORY_ROOT / "apps/web/dist")))
    if (directory / "index.html").is_file():
        # API・Swaggerを先に登録し、不明なAPIパスを画面のHTMLへ置き換えない。
        api.mount("/", StaticFiles(directory=directory, html=True), name="web")
