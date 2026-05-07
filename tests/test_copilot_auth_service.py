from __future__ import annotations
import asyncio
from pathlib import Path
import pytest
import app.services.copilot_auth_service as svc_module
from app.services.copilot_auth_service import COPILOT_OAUTH_CLIENT_ID, CopilotAuthService
from app.utils.github_url import normalize_github_api_base_url, normalize_github_oauth_base_url

class _Resp:
    def __init__(self, code, payload): self.status_code=code; self._payload=payload; self.content=b'1'; self.text=str(payload)
    def json(self): return self._payload
class _Client:
    def __init__(self,calls,factory): self.calls=calls; self.factory=factory
    async def __aenter__(self): return self
    async def __aexit__(self,*a): return False
    async def post(self,url,headers=None,json=None): self.calls.append({"url":url,"headers":headers,"json":json}); return self.factory(url,headers,json)

@pytest.fixture(autouse=True)
def _r(): svc_module._pending_authorizations.clear(); yield; svc_module._pending_authorizations.clear()

def test_normalize_github_api_base_url_cases(): assert normalize_github_api_base_url("https://github.company.com") == "https://github.company.com/api/v3"

def test_normalize_github_oauth_base_url_cases():
    assert normalize_github_oauth_base_url("") == "https://github.com"
    assert normalize_github_oauth_base_url(None) == "https://github.com"
    assert normalize_github_oauth_base_url("https://github.com") == "https://github.com"
    assert normalize_github_oauth_base_url("https://api.github.com") == "https://github.com"
    assert normalize_github_oauth_base_url("github.com") == "https://github.com"
    assert normalize_github_oauth_base_url("api.github.com") == "https://github.com"
    assert normalize_github_oauth_base_url("https://github.company.com") == "https://github.company.com"
    assert normalize_github_oauth_base_url("https://github.company.com/api/v3") == "https://github.company.com"
    assert normalize_github_oauth_base_url("github.company.com:8443") == "https://github.company.com:8443"

def test_start_uses_public_github_oauth_device_endpoint(monkeypatch):
    calls=[]
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,lambda *_:_Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})))
    s,p=asyncio.run(CopilotAuthService().start_authorization("u","")); assert s==200; assert calls[0]["url"]=="https://github.com/login/device/code"; assert calls[0]["json"]["client_id"]==COPILOT_OAUTH_CLIENT_ID; assert calls[0]["headers"]["Accept"]=="application/json"; assert p["auth_id"] and p["device_code"]

def test_start_uses_enterprise_oauth_device_endpoint(monkeypatch):
    calls=[]
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,lambda *_:_Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://ghe/login/device","expires_in":900,"interval":5})))
    asyncio.run(CopilotAuthService().start_authorization("u","https://github.company.com/api/v3")); assert calls[0]["url"]=="https://github.company.com/login/device/code"

def test_check_authorization_authorized_returns_oauth(monkeypatch):
    calls=[]
    def factory(_u,_h,j):
        if j.get("scope"): return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _Resp(200,{"access_token":"gho_TEST","token_type":"bearer","scope":"read:user"})
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory))
    svc=CopilotAuthService(); _,st=asyncio.run(svc.start_authorization("u","")); _,res=asyncio.run(svc.check_authorization("u",st["auth_id"],st["device_code"]))
    assert res["status"]=="authorized" and res["oauth"]["access"]=="gho_TEST" and res["token"]=="gho_TEST"

def test_check_pending(monkeypatch):
    calls=[]
    def factory(_u,_h,j):
        if j.get("scope"): return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _Resp(200,{"error":"authorization_pending"})
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory)); svc=CopilotAuthService(); _,st=asyncio.run(svc.start_authorization("u","")); _,res=asyncio.run(svc.check_authorization("u",st["auth_id"],st["device_code"])); assert res["status"]=="pending"

def test_check_slow_down(monkeypatch):
    calls=[]
    def factory(_u,_h,j):
        if j.get("scope"): return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _Resp(200,{"error":"slow_down"})
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory)); svc=CopilotAuthService(); _,st=asyncio.run(svc.start_authorization("u","")); _,res=asyncio.run(svc.check_authorization("u",st["auth_id"],st["device_code"])); assert res["status"]=="pending" and res["interval"]>=10

def test_check_declined(monkeypatch):
    calls=[]
    def factory(_u,_h,j):
        if j.get("scope"): return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _Resp(200,{"error":"access_denied"})
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory)); svc=CopilotAuthService(); _,st=asyncio.run(svc.start_authorization("u","")); _,res=asyncio.run(svc.check_authorization("u",st["auth_id"],st["device_code"])); assert res["status"]=="declined"

def test_regression_source_does_not_use_token_verification():
    assert "copilot/token_verification" not in Path("app/services/copilot_auth_service.py").read_text()
