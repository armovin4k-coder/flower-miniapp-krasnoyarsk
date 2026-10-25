from contextlib import asynccontextmanager
import asyncio
import csv
import io
import json
import re
import time
import httpx
from datetime import date, datetime, time as time_type, timedelta
from decimal import Decimal
from fastapi import BackgroundTasks, FastAPI, Depends, HTTPException, Request, Header, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from .config import settings
from .db import engine, get_session, SessionLocal
from .models import (
    Base,
    FlowerMovement,
    FlowerRecipe,
    FlowerRecipeLine,
    FlowerStockItem,
    Product,
    Order,
    OrderItem,
)
from .catalog import clean_variant_descriptions, extract_product_variants, resolve_variant_price
from .inventory import (
    consume_order_recipes,
    is_low_stock,
    quantize_money,
    weighted_average_cost,
)
from .security import validate_telegram_init_data, require_admin
from .payments import create_payment, fetch_payment
from .order_workflow import ORDER_STATUSES, change_order_status, mark_order_paid
from .telegram_notifications import notify_admins, notify_user

@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
        await c.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS categories_json TEXT NOT NULL DEFAULT '[]'"))
        await c.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS source_id VARCHAR(40)"))
        await c.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_products_source_id ON products (source_id)"))
        await c.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS fulfillment_method VARCHAR(20) NOT NULL DEFAULT 'delivery'"))
        await c.execute(text("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS variant_snapshot VARCHAR(160)"))
        await c.execute(text("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS variant_id_snapshot VARCHAR(40)"))
        await c.execute(text("ALTER TABLE flower_stock_items ADD COLUMN IF NOT EXISTS reserved_quantity NUMERIC(12, 3) NOT NULL DEFAULT 0"))
        await c.execute(text("ALTER TABLE flower_stock_items ADD COLUMN IF NOT EXISTS minimum_quantity NUMERIC(12, 3) NOT NULL DEFAULT 0"))
    async with SessionLocal() as s:
        if not await s.scalar(select(Product.id).limit(1)):
            s.add_all([
              Product(title="Букет «Нежность»",description="Розовые и белые сезонные цветы",category="Авторские букеты",price_rub=2490,image_url="https://images.unsplash.com/photo-1520763185298-1b434c919b9f?w=800",stock=10),
              Product(title="25 красных роз",description="Классика для особенного момента",category="Розы",price_rub=3990,image_url="https://images.unsplash.com/photo-1518895949257-7621c3c786d7?w=800",stock=10),
              Product(title="Весенний микс",description="Яркий сезонный букет",category="Сезонные",price_rub=1990,image_url="https://images.unsplash.com/photo-1490750967868-88aa4486c946?w=800",stock=10)])
            await s.commit()
    yield
    await engine.dispose()
app=FastAPI(title=settings.app_name,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["backend", "localhost", "127.0.0.1"],
)
app.add_middleware(CORSMiddleware,allow_origins=[settings.frontend_origin],allow_methods=["GET","POST","PATCH"],allow_headers=["Content-Type","Authorization","X-Telegram-Init-Data"])
def product_categories(p):
    return json.loads(p.categories_json or "[]") or [p.category]
def display_description(value):
    description = re.sub(r"\s+", " ", value).strip()
    description = re.sub(r"\s+(?=Букет из \d+\b)", "\n", description)
    description = re.sub(r"\s+(?=(?:К букету|Также|Цвет)\b)", "\n", description)
    return re.sub(r"\s+-\s+", " — ", description)
def pj(p):
    variants = extract_product_variants(p.description)
    description = clean_variant_descriptions(p.description) if variants else p.description
    return {"id":p.id,"title":p.title,"description":display_description(description),"category":p.category,"categories":product_categories(p),"price_rub":p.price_rub,"variants":variants,"image_url":p.image_url,"stock":p.stock}
@app.get("/api/health")
async def health(): return {"status":"ok"}
@app.get("/api/products")
async def products(s:AsyncSession=Depends(get_session)):
    rows=await s.scalars(select(Product).where(Product.active==True).order_by(Product.id))
    return [pj(p) for p in rows]
@app.get("/api/shop-info")
async def shop_info():
    return {"delivery_fee_rub":settings.delivery_fee_rub,"pickup_address":settings.pickup_address,"payments_enabled":settings.payments_enabled}
class CartItem(BaseModel):
    product_id:int
    quantity:int=Field(ge=1,le=20)
    variant_id:str|None=Field(default=None,max_length=40)
class OrderIn(BaseModel):
    items:list[CartItem]=Field(min_length=1,max_length=50)
    customer_name:str=Field(min_length=2,max_length=160)
    phone:str=Field(min_length=5,max_length=40)
    address:str=Field(default="",max_length=500)
    fulfillment_method:str=Field(default="delivery",pattern="^(delivery|pickup)$")
    delivery_at:str=Field(default="",max_length=100)
    note:str=Field(default="",max_length=1000)


class FlowerItemIn(BaseModel):
    title:str=Field(min_length=2,max_length=120)
    unit:str=Field(min_length=1,max_length=20)
    minimum_quantity:Decimal=Field(default=Decimal("0"),ge=0,max_digits=12,decimal_places=3)


class FlowerMovementIn(BaseModel):
    item_id:int
    movement_type:str=Field(pattern="^(receipt|expense|waste)$")
    quantity:Decimal=Field(gt=0,max_digits=12,decimal_places=3)
    unit_cost_rub:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    note:str=Field(default="",max_length=500)


class FlowerRecipeLineIn(BaseModel):
    item_id:int
    quantity:Decimal=Field(gt=0,max_digits=12,decimal_places=3)


class FlowerRecipeIn(BaseModel):
    product_id:int
    variant_id:str|None=Field(default=None,max_length=40)
    lines:list[FlowerRecipeLineIn]=Field(max_length=50)


@app.get("/api/admin/stock/overview",dependencies=[Depends(require_admin)])
async def stock_overview(s:AsyncSession=Depends(get_session)):
    items=list(await s.scalars(
        select(FlowerStockItem).where(FlowerStockItem.active.is_(True)).order_by(FlowerStockItem.title)
    ))
    products=list(await s.scalars(
        select(Product).where(Product.active.is_(True)).order_by(Product.title)
    ))
    recipes=list(await s.scalars(
        select(FlowerRecipe).options(selectinload(FlowerRecipe.lines)).order_by(FlowerRecipe.product_id)
    ))
    stock_items_by_id={item.id:item for item in items}
    recipe_products={product.id:product for product in products}
    movements=list(await s.scalars(
        select(FlowerMovement).order_by(FlowerMovement.created_at.desc(),FlowerMovement.id.desc()).limit(100)
    ))
    configured_keys={(recipe.product_id,recipe.variant_id) for recipe in recipes}
    recipe_rows=[]
    for recipe in recipes:
        product=recipe_products.get(recipe.product_id)
        if product is None:
            continue
        variants=extract_product_variants(product.description)
        variant=next((value for value in variants if value["id"]==recipe.variant_id),None)
        recipe_rows.append({
            "product_id":product.id,
            "product_title":product.title,
            "variant_id":recipe.variant_id or None,
            "variant_label":str(variant["label"]) if variant else None,
            "lines":[
                {
                    "item_id":line.item_id,
                    "item_title":stock_items_by_id[line.item_id].title if line.item_id in stock_items_by_id else "Удалённый цветок",
                    "unit":stock_items_by_id[line.item_id].unit if line.item_id in stock_items_by_id else "",
                    "quantity":float(line.quantity),
                }
                for line in recipe.lines
            ],
        })
    missing_recipes=[]
    for product in products:
        variants=extract_product_variants(product.description)
        variant_keys=[str(variant["id"]) for variant in variants] if variants else [""]
        for variant_id in variant_keys:
            if (product.id,variant_id) not in configured_keys:
                variant=next((v for v in variants if v["id"]==variant_id),None)
                missing_recipes.append({
                    "product_id":product.id,
                    "product_title":product.title,
                    "variant_id":variant_id or None,
                    "variant_label":str(variant["label"]) if variant else None,
                })
    total_value=sum(
        (item.quantity*item.average_cost_rub for item in items),
        Decimal("0"),
    )
    return {
        "items":[
            {
                "id":item.id,"title":item.title,"unit":item.unit,"quantity":float(item.quantity),
                "reserved_quantity":float(item.reserved_quantity),
                "available_quantity":float(item.quantity-item.reserved_quantity),
                "average_cost_rub":float(item.average_cost_rub),
                "minimum_quantity":float(item.minimum_quantity),
                "is_low_stock":is_low_stock(
                    item.quantity,item.reserved_quantity,item.minimum_quantity
                ),
                "stock_value_rub":float(quantize_money(item.quantity*item.average_cost_rub)),
            }
            for item in items
        ],
        "total_value_rub":float(quantize_money(total_value)),
        "recipes":recipe_rows,
        "missing_recipes":missing_recipes,
        "movements":[
            {
                "id":movement.id,"item_id":movement.item_id,
                "item_title":stock_items_by_id[movement.item_id].title if movement.item_id in stock_items_by_id else "Удалённый цветок",
                "unit":stock_items_by_id[movement.item_id].unit if movement.item_id in stock_items_by_id else "",
                "type":movement.movement_type,"quantity":float(movement.quantity),
                "unit_cost_rub":float(movement.unit_cost_rub),
                "total_cost_rub":float(movement.total_cost_rub),
                "note":movement.note,"order_id":movement.order_id,
                "is_reversed":movement.is_reversed,"created_at":movement.created_at.isoformat(),
            }
            for movement in movements
        ],
    }


def stock_report_query(s:AsyncSession,start_date:date|None,end_date:date|None):
    statement=select(FlowerMovement).order_by(FlowerMovement.created_at,FlowerMovement.id)
    if start_date:
        statement=statement.where(
            FlowerMovement.created_at>=datetime.combine(start_date,time_type.min)
        )
    if end_date:
        statement=statement.where(
            FlowerMovement.created_at<datetime.combine(end_date+timedelta(days=1),time_type.min)
        )
    return statement


def summarize_stock_movements(movements:list[FlowerMovement]):
    totals={key:Decimal("0") for key in ("receipt","expense","waste","sale","sale_return")}
    quantities={key:Decimal("0") for key in ("receipt","expense","waste","sale","sale_return")}
    for movement in movements:
        if movement.movement_type in totals:
            totals[movement.movement_type]+=movement.total_cost_rub
            quantities[movement.movement_type]+=movement.quantity
    sale_cost=quantize_money(totals["sale"]-totals["sale_return"])
    return {
        "movement_count":len(movements),
        "receipt_value_rub":float(quantize_money(totals["receipt"])),
        "expense_value_rub":float(quantize_money(totals["expense"])),
        "waste_value_rub":float(quantize_money(totals["waste"])),
        "sales_cost_rub":float(sale_cost),
        "receipt_quantity":float(quantities["receipt"]),
        "expense_quantity":float(quantities["expense"]),
        "waste_quantity":float(quantities["waste"]),
        "sold_quantity":float(quantities["sale"]-quantities["sale_return"]),
    }


@app.get("/api/admin/stock/report",dependencies=[Depends(require_admin)])
async def stock_report(
    start_date:date|None=None,
    end_date:date|None=None,
    s:AsyncSession=Depends(get_session),
):
    if start_date and end_date and start_date>end_date:
        raise HTTPException(422,"Начало периода не может быть позже окончания")
    movements=list(await s.scalars(stock_report_query(s,start_date,end_date)))
    summary=summarize_stock_movements(movements)
    summary["start_date"]=start_date.isoformat() if start_date else None
    summary["end_date"]=end_date.isoformat() if end_date else None
    return summary


@app.get("/api/admin/stock/export.csv",dependencies=[Depends(require_admin)])
async def export_stock_movements(
    start_date:date|None=None,
    end_date:date|None=None,
    s:AsyncSession=Depends(get_session),
):
    if start_date and end_date and start_date>end_date:
        raise HTTPException(422,"Начало периода не может быть позже окончания")
    movements=list(await s.scalars(stock_report_query(s,start_date,end_date)))
    items={item.id:item for item in await s.scalars(select(FlowerStockItem))}
    output=io.StringIO(newline="")
    writer=csv.writer(output)
    writer.writerow([
        "Дата","Цветок","Единица","Операция","Количество",
        "Цена за единицу","Сумма","Комментарий","Заказ","Отменена",
    ])
    labels={
        "receipt":"Приход","expense":"Расход","waste":"Брак / списание",
        "sale":"Продажа","sale_return":"Возврат продажи",
        "reservation":"Резерв","reservation_release":"Снятие резерва",
    }
    for movement in movements:
        item=items.get(movement.item_id)
        writer.writerow([
            movement.created_at.isoformat(),
            item.title if item else "Удалённый цветок",
            item.unit if item else "",
            labels.get(movement.movement_type,movement.movement_type),
            str(movement.quantity),str(movement.unit_cost_rub),str(movement.total_cost_rub),
            movement.note,movement.order_id or "",str(movement.is_reversed),
        ])
    return Response(
        content="\ufeff"+output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":'attachment; filename="flower-stock-movements.csv"'},
    )


@app.post("/api/admin/stock/items",dependencies=[Depends(require_admin)])
async def create_flower_item(p:FlowerItemIn,s:AsyncSession=Depends(get_session)):
    item=FlowerStockItem(
        title=p.title.strip(),unit=p.unit.strip(),
        minimum_quantity=p.minimum_quantity.quantize(Decimal("0.001")),
    )
    s.add(item)
    try:
        await s.commit()
    except IntegrityError as exc:
        await s.rollback()
        raise HTTPException(409,"Цветок с таким названием уже есть на складе") from exc
    await s.refresh(item)
    return {
        "id":item.id,"title":item.title,"unit":item.unit,"quantity":0,
        "reserved_quantity":0,"available_quantity":0,"average_cost_rub":0,
        "minimum_quantity":float(item.minimum_quantity),"is_low_stock":False,
        "stock_value_rub":0,
    }


@app.post("/api/admin/stock/movements",dependencies=[Depends(require_admin)])
async def create_flower_movement(p:FlowerMovementIn,s:AsyncSession=Depends(get_session)):
    item=await s.scalar(
        select(FlowerStockItem)
        .where(FlowerStockItem.id==p.item_id,FlowerStockItem.active.is_(True))
        .with_for_update()
    )
    if item is None:
        raise HTTPException(404,"Цветок не найден")
    quantity=p.quantity.quantize(Decimal("0.001"))
    if p.movement_type=="receipt":
        if p.unit_cost_rub is None:
            raise HTTPException(422,"Укажите закупочную цену за единицу")
        unit_cost=quantize_money(p.unit_cost_rub)
        item.average_cost_rub=weighted_average_cost(
            item.quantity,item.average_cost_rub,quantity,unit_cost
        )
        item.quantity+=quantity
    else:
        available=item.quantity-item.reserved_quantity
        if available<quantity:
            raise HTTPException(409,f"Недостаточно свободного остатка: {available} {item.unit}")
        unit_cost=quantize_money(item.average_cost_rub)
        item.quantity-=quantity
    total_cost=quantize_money(quantity*unit_cost)
    movement=FlowerMovement(
        item_id=item.id,movement_type=p.movement_type,quantity=quantity,
        unit_cost_rub=unit_cost,total_cost_rub=total_cost,note=p.note.strip(),
    )
    s.add(movement)
    await s.commit()
    return {
        "id":movement.id,"quantity":float(item.quantity),
        "reserved_quantity":float(item.reserved_quantity),
        "available_quantity":float(item.quantity-item.reserved_quantity),
        "average_cost_rub":float(item.average_cost_rub),
        "stock_value_rub":float(quantize_money(item.quantity*item.average_cost_rub)),
    }


@app.put("/api/admin/stock/recipes",dependencies=[Depends(require_admin)])
async def save_flower_recipe(p:FlowerRecipeIn,s:AsyncSession=Depends(get_session)):
    product=await s.scalar(
        select(Product).where(Product.id==p.product_id,Product.active.is_(True))
    )
    if product is None:
        raise HTTPException(404,"Букет не найден")
    variants=extract_product_variants(product.description)
    if variants and p.variant_id not in {str(variant["id"]) for variant in variants}:
        raise HTTPException(422,"Выберите существующий вариант букета")
    if not variants and p.variant_id is not None:
        raise HTTPException(422,"У этого букета нет вариантов")
    item_ids=[line.item_id for line in p.lines]
    if len(item_ids)!=len(set(item_ids)):
        raise HTTPException(422,"Цветок может быть указан в рецепте только один раз")
    if item_ids:
        available=set(await s.scalars(
            select(FlowerStockItem.id).where(
                FlowerStockItem.id.in_(item_ids),FlowerStockItem.active.is_(True)
            )
        ))
        if available!=set(item_ids):
            raise HTTPException(422,"Выберите активные позиции цветка со склада")
    variant_key=p.variant_id or ""
    recipe=await s.scalar(
        select(FlowerRecipe)
        .options(selectinload(FlowerRecipe.lines))
        .where(FlowerRecipe.product_id==p.product_id,FlowerRecipe.variant_id==variant_key)
    )
    if recipe is None:
        recipe=FlowerRecipe(product_id=p.product_id,variant_id=variant_key,lines=[])
        s.add(recipe)
    else:
        recipe.lines.clear()
    recipe.lines.extend(
        FlowerRecipeLine(item_id=line.item_id,quantity=line.quantity.quantize(Decimal("0.001")))
        for line in p.lines
    )
    await s.commit()
    return {"ok":True,"configured":True,"line_count":len(p.lines)}


@app.post("/api/orders")
async def order_create(p:OrderIn,background_tasks:BackgroundTasks,x_telegram_init_data:str|None=Header(default=None),s:AsyncSession=Depends(get_session)):
    if not x_telegram_init_data: raise HTTPException(401,"Откройте магазин из Telegram")
    user=validate_telegram_init_data(x_telegram_init_data)
    if p.fulfillment_method=="delivery" and len(p.address.strip())<5:
        raise HTTPException(422,"Укажите адрес доставки")
    quantities={}
    for i in p.items: quantities[i.product_id]=quantities.get(i.product_id,0)+i.quantity
    if any(quantity>20 for quantity in quantities.values()):
        raise HTTPException(422,"Количество одного товара не может превышать 20")
    rows=await s.scalars(select(Product).where(Product.id.in_(quantities),Product.active==True).order_by(Product.id).with_for_update())
    products={x.id:x for x in rows}
    if len(products)!=len(quantities): raise HTTPException(400,"Товар недоступен")
    subtotal=0
    item_prices=[]
    for item in p.items:
        product=products[item.product_id]
        try:
            price,variant_label=resolve_variant_price(product.description,product.price_rub,item.variant_id)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        subtotal+=price*item.quantity
        item_prices.append((item,price,variant_label))
    for pid,q in quantities.items():
        if products[pid].stock<q: raise HTTPException(409,f"Недостаточно товара: {products[pid].title}")
    if subtotal<settings.min_order_rub: raise HTTPException(400,"Заказ меньше минимальной суммы")
    delivery=settings.delivery_fee_rub if p.fulfillment_method=="delivery" else 0
    o=Order(telegram_user_id=int(user["id"]),customer_name=p.customer_name.strip(),phone=p.phone.strip(),address=p.address.strip(),fulfillment_method=p.fulfillment_method,delivery_at=p.delivery_at,note=p.note,subtotal_rub=subtotal,delivery_rub=delivery,total_rub=subtotal+delivery,status="new",payment_status="awaiting_confirmation")
    s.add(o); await s.flush()
    for pid,q in quantities.items(): products[pid].stock-=q
    order_flower_lines=[]
    for item,price,variant_label in item_prices:
        product=products[item.product_id]
        order_item=OrderItem(order_id=o.id,product_id=item.product_id,title_snapshot=product.title,variant_snapshot=variant_label,variant_id_snapshot=item.variant_id,unit_price_rub=price,quantity=item.quantity)
        s.add(order_item)
        order_flower_lines.append((order_item,item.variant_id,item.quantity))
    await s.flush()
    try:
        uncovered=await consume_order_recipes(s,o.id,order_flower_lines,products)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    if uncovered and await s.scalar(
        select(FlowerStockItem.id)
        .where(FlowerStockItem.active.is_(True))
        .limit(1)
    ):
        titles=", ".join(products[pid].title for pid in uncovered)
        raise HTTPException(
            409,
            f"Сначала настройте рецепты списания цветов для: {titles}",
        )
    await s.commit()
    method="Самовывоз" if o.fulfillment_method=="pickup" else "Доставка"
    address=o.address or settings.pickup_address
    item_summary="\n".join(
        f"• {products[item.product_id].title}"
        f"{f' — {variant_label}' if variant_label else ''} × {item.quantity} — {price * item.quantity} ₽"
        for item,price,variant_label in item_prices
    )
    recipe_warning=(
        "\n⚠️ Не настроен рецепт списания цветов: "
        + ", ".join(products[pid].title for pid in uncovered)
        + ". Настройте его в разделе /admin."
        if uncovered else ""
    )
    background_tasks.add_task(notify_admins,settings.bot_token,settings.admin_ids,f"Новый заказ №{o.id}\n{item_summary}\n{method}: {address}\nК оплате: {o.total_rub} ₽{recipe_warning}\nОткройте /orders для обработки.")
    background_tasks.add_task(notify_user,settings.bot_token,int(user["id"]),f"Заказ №{o.id} принят и ожидает подтверждения флористом. После подтверждения администратор пришлёт способ оплаты.")
    return {"order_id":o.id,"total_rub":o.total_rub,"payment_enabled":settings.payments_enabled,"status":o.status,"unconfigured_inventory_products":uncovered}
@app.post("/api/orders/{oid}/pay")
async def pay(oid:int,x_telegram_init_data:str|None=Header(default=None),s:AsyncSession=Depends(get_session)):
    if not x_telegram_init_data: raise HTTPException(401,"Откройте магазин из Telegram")
    u=validate_telegram_init_data(x_telegram_init_data); o=await s.get(Order,oid)
    if not o or o.telegram_user_id!=int(u["id"]): raise HTTPException(404,"Заказ не найден")
    if o.status not in {"confirmed","preparing","ready_for_pickup","out_for_delivery"}:
        raise HTTPException(409,"Оплата доступна после подтверждения заказа")
    if o.payment_status=="succeeded": return {"status":"succeeded"}
    p=await create_payment(o.id,o.total_rub); o.payment_id=p["id"]; o.payment_status=p["status"]; await s.commit()
    return {"payment_id":p["id"],"status":p["status"],"confirmation_url":p.get("confirmation",{}).get("confirmation_url")}
@app.post("/api/payments/yookassa/webhook")
async def webhook(req:Request,s:AsyncSession=Depends(get_session)):
    try: pid=str((await req.json())["object"]["id"])
    except (ValueError,KeyError,TypeError): raise HTTPException(400,"Некорректный webhook")
    p=await fetch_payment(pid); meta=p.get("metadata") or {}
    try: oid=int(meta["order_id"])
    except (KeyError,ValueError,TypeError): raise HTTPException(400,"Нет metadata заказа")
    o=await s.get(Order,oid)
    if not o or o.payment_id!=pid: return {"ok":True}
    amount=p.get("amount",{})
    if amount.get("currency")!="RUB" or round(float(amount.get("value","0"))*100)!=o.total_rub*100:
        raise HTTPException(400,"Сумма платежа не совпадает")
    if p.get("status")=="succeeded" and p.get("paid") is True:
        o.payment_status="succeeded"
    elif p.get("status")=="canceled": o.payment_status="canceled"; o.status="payment_canceled"
    await s.commit()
    return {"ok":True}
class AIIn(BaseModel):
    occasion:str=Field(min_length=2,max_length=200)
    budget_rub:int=Field(ge=500,le=1000000)
    preferences:str=Field(default="",max_length=500)
_ai_requests: dict[int, list[float]] = {}
_ai_rate_limit_lock = asyncio.Lock()
_AI_RATE_LIMIT = 8
_AI_RATE_WINDOW_SECONDS = 300

async def enforce_ai_rate_limit(user_id: int) -> None:
    now = time.monotonic()
    async with _ai_rate_limit_lock:
        if user_id not in _ai_requests and len(_ai_requests) >= 1000:
            for tracked_user, timestamps in list(_ai_requests.items()):
                if not timestamps or now - timestamps[-1] >= _AI_RATE_WINDOW_SECONDS:
                    del _ai_requests[tracked_user]
            if len(_ai_requests) >= 1000:
                raise HTTPException(503, "Сервис временно перегружен. Попробуйте позже.")
        recent = [
            timestamp
            for timestamp in _ai_requests.get(user_id, [])
            if now - timestamp < _AI_RATE_WINDOW_SECONDS
        ]
        if len(recent) >= _AI_RATE_LIMIT:
            raise HTTPException(429, "Слишком много запросов. Попробуйте через несколько минут.")
        recent.append(now)
        _ai_requests[user_id] = recent

@app.post("/api/ai/recommend")
async def ai(
    p: AIIn,
    x_telegram_init_data: str | None = Header(default=None),
):
    if not x_telegram_init_data:
        raise HTTPException(401, "Откройте AI-подбор из Telegram")
    user = validate_telegram_init_data(x_telegram_init_data, max_age_seconds=3600)
    await enforce_ai_rate_limit(int(user["id"]))
    if not settings.ai_api_key: return {"recommendation":"Расскажите, кому выбираете букет и какой повод. AI пока не подключён; ориентируйтесь на каталог и уточните наличие у флориста."}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                settings.ai_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + settings.ai_api_key},
                json={
                    "model": settings.ai_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Ты консультант цветочного магазина Красноярска. Не выдумывай наличие и цены; не подтверждай платежи.",
                        },
                        {
                            "role": "user",
                            "content": f"Повод: {p.occasion}. Бюджет: {p.budget_rub} RUB. Пожелания: {p.preferences}. Ответь по-русски кратко.",
                        },
                    ],
                    "max_tokens": 300,
                },
            )
        if response.status_code >= 400:
            raise HTTPException(502, "AI временно недоступен")
        recommendation = response.json()["choices"][0]["message"]["content"]
    except httpx.HTTPError as exc:
        raise HTTPException(502, "AI временно недоступен") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(502, "AI вернул некорректный ответ") from exc
    return {"recommendation": recommendation}
@app.get("/api/admin/orders",dependencies=[Depends(require_admin)])
async def admin_orders(s:AsyncSession=Depends(get_session)):
    rows=await s.scalars(select(Order).options(selectinload(Order.items)).order_by(Order.id.desc()).limit(100))
    return [{"id":o.id,"name":o.customer_name,"phone":o.phone,"address":o.address,"fulfillment_method":o.fulfillment_method,"items":[{"title":i.title_snapshot,"variant":i.variant_snapshot,"quantity":i.quantity,"price_rub":i.unit_price_rub} for i in o.items],"total_rub":o.total_rub,"status":o.status,"payment_status":o.payment_status} for o in rows]
class ProductIn(BaseModel):
    title:str=Field(min_length=2,max_length=160); description:str=""; category:str="Букеты"
    price_rub:int=Field(ge=1); image_url:str=""; stock:int=Field(ge=0)
@app.post("/api/admin/products",dependencies=[Depends(require_admin)])
async def admin_product(p:ProductIn,s:AsyncSession=Depends(get_session)):
    x=Product(**p.model_dump(),categories_json=json.dumps([p.category],ensure_ascii=False));s.add(x);await s.commit();await s.refresh(x);return pj(x)
class CatalogImportItem(BaseModel):
    source_id:str=Field(min_length=1,max_length=40)
    title:str=Field(min_length=2,max_length=160)
    description:str=""
    categories:list[str]=Field(min_length=1,max_length=20)
    price_rub:int=Field(ge=1)
    image_url:str=Field(default="",max_length=1000)
class CatalogImport(BaseModel):
    items:list[CatalogImportItem]=Field(min_length=1,max_length=500)
@app.post("/api/admin/products/import",dependencies=[Depends(require_admin)])
async def admin_import_products(payload:CatalogImport,s:AsyncSession=Depends(get_session)):
    source_ids=[item.source_id for item in payload.items]
    if len(source_ids)!=len(set(source_ids)):
        raise HTTPException(400,"Повторяются source_id товаров")
    rows=list(await s.scalars(select(Product)))
    existing={p.source_id:p for p in rows if p.source_id}
    semantic_categories=("Розы","Монобукеты","Сборные букеты","Композиции","Свадебные букеты","Вазы","Шары","1 сентября")
    for product in rows:
        if product.source_id not in source_ids:
            product.active=False
    created=0
    for item in payload.items:
        category=next((name for name in semantic_categories if name in item.categories),"Букеты")
        product=existing.get(item.source_id)
        if product is None:
            product=Product(source_id=item.source_id,stock=0)
            s.add(product)
            created+=1
        product.title=item.title
        product.description=item.description
        product.category=category
        product.categories_json=json.dumps(item.categories,ensure_ascii=False)
        product.price_rub=item.price_rub
        product.image_url=item.image_url
        product.active=True
    await s.commit()
    return {"created":created,"updated":len(payload.items)-created,"total":len(payload.items),"stock_initialized_to_zero":created}
@app.patch("/api/admin/orders/{oid}",dependencies=[Depends(require_admin)])
async def admin_status(oid:int,status:str,background_tasks:BackgroundTasks,s:AsyncSession=Depends(get_session)):
    if status not in ORDER_STATUSES: raise HTTPException(400,"Недопустимый статус")
    o=await s.scalar(select(Order).options(selectinload(Order.items)).where(Order.id==oid).with_for_update())
    if not o: raise HTTPException(404,"Заказ не найден")
    try:
        changed=await change_order_status(s,o,status)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    await s.commit()
    if changed:
        label=ORDER_STATUSES[status]
        background_tasks.add_task(notify_user,settings.bot_token,o.telegram_user_id,f"Статус заказа №{o.id}: {label}.")
    return {"ok":True,"status":status}
@app.patch("/api/admin/orders/{oid}/paid",dependencies=[Depends(require_admin)])
async def admin_mark_paid(oid:int,background_tasks:BackgroundTasks,s:AsyncSession=Depends(get_session)):
    o=await s.get(Order,oid)
    if not o: raise HTTPException(404,"Заказ не найден")
    try:
        changed=await mark_order_paid(o,s)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    await s.commit()
    if changed:
        background_tasks.add_task(notify_user,settings.bot_token,o.telegram_user_id,f"Оплата заказа №{o.id} отмечена.")
    return {"ok":True,"payment_status":o.payment_status}
@app.patch("/api/admin/products/{pid}/stock",dependencies=[Depends(require_admin)])
async def admin_stock(pid:int,stock:int=Query(ge=0),s:AsyncSession=Depends(get_session)):
    product=await s.scalar(select(Product).where(Product.id==pid).with_for_update())
    if not product: raise HTTPException(404,"Товар не найден")
    product.stock=stock
    await s.commit()
    return {"id":product.id,"stock":product.stock}
