import uuid
from decimal import Decimal
import httpx
from fastapi import HTTPException
from .config import settings
BASE = "https://api.yookassa.ru/v3"
async def create_payment(order_id: int, total_rub: int):
    if not settings.payments_enabled or not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        raise HTTPException(503, "Онлайн-оплата не настроена")
    payload = {"amount":{"value":f"{Decimal(total_rub):.2f}","currency":"RUB"},
      "capture":True,"confirmation":{"type":"redirect","return_url":settings.yookassa_return_url},
      "description":f"Цветочный магазин — заказ #{order_id}"[:128],
      "metadata":{"order_id":str(order_id)},"payment_method_data":{"type":"sbp"}}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(BASE+"/payments", json=payload,
          headers={"Idempotence-Key":str(uuid.uuid4())},
          auth=(settings.yookassa_shop_id,settings.yookassa_secret_key))
    if r.status_code >= 400: raise HTTPException(502,"Не удалось создать платёж")
    return r.json()
async def fetch_payment(payment_id: str):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(BASE+"/payments/"+payment_id,
          auth=(settings.yookassa_shop_id,settings.yookassa_secret_key))
    if r.status_code >= 400: raise HTTPException(502,"Не удалось проверить платёж")
    return r.json()
