from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import reconcile_service as svc
from app.services import price_service

router = APIRouter(prefix="/prices", tags=["prices"])


class AdjustIn(BaseModel):
    sku_code: str
    price: float
    reason: str | None = None


@router.get("/book")
def price_book():
    return svc.list_price_book()


@router.get("/history")
def history(sku_code: str | None = Query(default=None)):
    return svc.list_history(sku_code)


@router.get("/verify")
def verify():
    return price_service.verify_book()


@router.post("/adjust")
def adjust(body: AdjustIn):
    return price_service.adjust_price(body.model_dump())
