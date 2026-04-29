from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

from app.config import UI_DIR

router = APIRouter(tags=["pages"])


@router.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/kiosk")


@router.get("/kiosk", include_in_schema=False)
def kiosk_page() -> FileResponse:
    return FileResponse(UI_DIR / "kiosk.html")


@router.get("/table", include_in_schema=False)
def table_page() -> FileResponse:
    return FileResponse(UI_DIR / "table.html")
