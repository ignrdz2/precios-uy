from pydantic import BaseModel, ConfigDict


class SupermarketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    base_url: str
