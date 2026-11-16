from fastapi import APIRouter
from pydantic import BaseModel

from app.services import apply_service as svc

router = APIRouter(prefix="/apply", tags=["apply"])


class ClockIn(BaseModel):
    business_clock: str


class StrategyIn(BaseModel):
    drift_strategy: str


@router.get("/clock")
def get_clock():
    return svc.get_clock()


@router.put("/clock")
def set_clock(body: ClockIn):
    return svc.set_clock(body.business_clock)


@router.get("/strategy")
def get_strategy():
    return svc.get_strategy()


@router.put("/strategy")
def set_strategy(body: StrategyIn):
    return svc.set_strategy(body.drift_strategy)


@router.get("/batches")
def list_batches():
    return svc.list_batches()


@router.get("/batches/{batch_id}/lines")
def list_batch_lines(batch_id: int):
    return svc.list_batch_lines(batch_id)


@router.post("/due")
def apply_due():
    return svc.apply_due()
