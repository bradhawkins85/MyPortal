from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CompanyAddressInput(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    street: str = Field(min_length=1, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postcode: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=100)

    @field_validator("label", "street", "city", "state", "postcode", "country", mode="before")
    @classmethod
    def strip_text(cls, value):
        if value is None:
            return None
        return str(value).strip()


class CompanyAddressResponse(CompanyAddressInput):
    id: int
    company_id: int
