from __future__ import annotations

from fastapi import APIRouter, Request

from app.models.schemas import TelegramUpdate, WebhookResult

router = APIRouter(prefix="/webhook", tags=["telegram"])


@router.post("/telegram", response_model=WebhookResult)
async def telegram_webhook(request: Request, update: TelegramUpdate) -> WebhookResult:
    app_router = request.app.state.update_router
    return app_router.handle_update(update)
