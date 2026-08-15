from pydantic import BaseModel


class InvokeRequest(BaseModel):
    thread_id: str
    message: str
