from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

# Kept in step with app/services/locale_service.py: "auto" is the default,
# the three tags are the Portal's languages (Traditional Chinese means Hong
# Kong conventions). The assistant answers in the same language.
UiLocaleChoice = Literal["auto", "en", "zh-Hans", "zh-Hant-HK"]


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return str(value or "").strip().lower()


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6)
    nickname: str = Field(None, max_length=64)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return str(value or "").strip().lower()


class MePreferences(BaseModel):
    """What the member saved: ``auto`` until they choose."""

    ui_locale: UiLocaleChoice = "auto"


class MeLocale(BaseModel):
    """What this request resolved to (preference, else the browser, else English)."""

    ui_locale: str
    ui_source: str
    reply_language: str
    reply_language_strict: bool


class MeResponse(BaseModel):
    id: int
    username: str
    nickname: Optional[str] = None
    role: str
    onboarding_completed: bool = False
    preferences: MePreferences = Field(default_factory=MePreferences)
    locale: Optional[MeLocale] = None


class PreferencesUpdate(BaseModel):
    """A partial update: a key that is absent (or null) keeps its stored value."""

    ui_locale: Optional[UiLocaleChoice] = None
