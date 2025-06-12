
from pydantic import BaseModel

class AuthUser(BaseModel):
    user_id: str
    user_role: str

class FinishOrder(BaseModel):
    order_id: str
    order_status: str