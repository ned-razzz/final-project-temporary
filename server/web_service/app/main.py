from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import menu as menu_router
from app.routers import orders as orders_router
from app.routers import pages as pages_router
from app.routers import tables as tables_router

app = FastAPI(title="BREW order service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(menu_router.router)
app.include_router(tables_router.router)
app.include_router(orders_router.router)
app.include_router(pages_router.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
