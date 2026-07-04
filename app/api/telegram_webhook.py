from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request

from app.models.schemas import TelegramUpdate, WebhookResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["telegram"])


@router.post("/telegram", response_model=WebhookResult)
async def telegram_webhook(
    request: Request,
    update: TelegramUpdate,
    background_tasks: BackgroundTasks,
) -> WebhookResult:
    try:
        app_router = request.app.state.update_router
        return app_router.handle_update(update, background_tasks)
    except Exception:
        logger.exception("Unhandled Telegram webhook error")
        return WebhookResult(status="accepted", detail="internal_error")
