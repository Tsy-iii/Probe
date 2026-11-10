from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


PositiveInt = Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]
Amount = Annotated[int, Field(strict=True, gt=0, le=9_223_372_036_854_775_807)]
EventId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")]


class CreateOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: PositiveInt = Field(description="商品 ID；商品必须存在且在售。", examples=[1])
    quantity: PositiveInt = Field(description="购买数量，为正整数；成功时扣减该数量库存。", examples=[2])


class PaymentCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: EventId = Field(description="支付通知的稳定事件 ID。相同通知重发时不改变 ID；只允许 ASCII 字母、数字和 _ . : -。", examples=["demo-pay-001"])
    order_id: UUID = Field(description="创建订单接口返回的完整 UUID。")
    amount_cents: Amount = Field(description="整数分金额，必须等于订单 total_amount_cents；例如 39.80 元填写 3980。", examples=[3980])


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    price_cents: int
    stock: int
    is_active: bool


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: int
    quantity: int
    total_amount_cents: int
    status: Literal["pending", "paid", "cancelled"]
    created_at: datetime
    updated_at: datetime


class PaymentOut(BaseModel):
    order: OrderOut
    duplicate: bool
