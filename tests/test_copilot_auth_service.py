from __future__ import annotations
import json
import asyncio
from pathlib import Path
import pytest
import app.services.copilot_auth_service as svc_module
from app.services.copilot_auth_service import COPILOT_OAUTH_CLIENT_IDS, CopilotAuthService
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
    s,p=asyncio.run(CopilotAuthService().start_authorization("u","")); assert s==200; assert calls[0]["url"]=="https://github.com/login/device/code"; assert calls[0]["json"]["client_id"]==COPILOT_OAUTH_CLIENT_IDS["opencode"]; assert calls[0]["headers"]["Accept"]=="application/json"; assert p["auth_id"] and p["device_code"]

def test_start_ignores_enterprise_base_url_for_copilot_oauth(monkeypatch):
    calls=[]
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,lambda *_:_Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})))
    status, payload = asyncio.run(CopilotAuthService().start_authorization("u","https://github.company.com/api/v3"))
    assert status == 200
    assert calls[0]["url"]=="https://github.com/login/device/code"
    rec = svc_module._pending_authorizations[payload["auth_id"]]
    assert rec["access_token_url"] == "https://github.com/login/oauth/access_token"
    assert rec["oauth_base_url"] == "https://github.com"
    assert payload["auth_id"] and payload["device_code"] and payload["user_code"]
    assert payload["runtime_type"] == "opencode"

def test_start_runtime_specific_client_ids(monkeypatch):
    calls=[]
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,lambda *_:_Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})))
    svc=CopilotAuthService()
    asyncio.run(svc.start_authorization("u","",runtime_type="native"))
    asyncio.run(svc.start_authorization("u","",runtime_type="efp"))
    asyncio.run(svc.start_authorization("u","",runtime_type="opencode"))
    asyncio.run(svc.start_authorization("u","",runtime_type=None))
    assert calls[0]["json"]["client_id"] == COPILOT_OAUTH_CLIENT_IDS["native"]
    assert calls[1]["json"]["client_id"] == COPILOT_OAUTH_CLIENT_IDS["native"]
    assert calls[2]["json"]["client_id"] == COPILOT_OAUTH_CLIENT_IDS["opencode"]
    assert calls[3]["json"]["client_id"] == COPILOT_OAUTH_CLIENT_IDS["opencode"]

def test_check_authorization_authorized_returns_oauth(monkeypatch):
    calls=[]
    def factory(_u,_h,j):
        if j.get("scope"): return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _Resp(200,{"access_token":"gho_TEST","token_type":"bearer","scope":"read:user"})
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory))
    svc=CopilotAuthService(); _,st=asyncio.run(svc.start_authorization("u","")); svc_module._pending_authorizations[st["auth_id"]]["latest_check"] = 0; _,res=asyncio.run(svc.check_authorization("u",st["auth_id"],st["device_code"]))
    assert res["status"]=="authorized" and res["oauth"]["access"]=="gho_TEST" and res["token"]=="gho_TEST"

def test_check_pending(monkeypatch):
    calls=[]
    def factory(_u,_h,j):
        if j.get("scope"): return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _Resp(200,{"error":"authorization_pending"})
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory)); svc=CopilotAuthService(); _,st=asyncio.run(svc.start_authorization("u","")); svc_module._pending_authorizations[st["auth_id"]]["latest_check"] = 0; _,res=asyncio.run(svc.check_authorization("u",st["auth_id"],st["device_code"])); assert res["status"]=="pending"

def test_check_slow_down(monkeypatch):
    calls=[]
    def factory(_u,_h,j):
        if j.get("scope"): return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _Resp(200,{"error":"slow_down"})
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory)); svc=CopilotAuthService(); _,st=asyncio.run(svc.start_authorization("u","")); svc_module._pending_authorizations[st["auth_id"]]["latest_check"] = 0; _,res=asyncio.run(svc.check_authorization("u",st["auth_id"],st["device_code"])); assert res["status"]=="pending" and res["interval"]>=10

def test_check_declined(monkeypatch):
    calls=[]
    def factory(_u,_h,j):
        if j.get("scope"): return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _Resp(200,{"error":"access_denied"})
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory)); svc=CopilotAuthService(); _,st=asyncio.run(svc.start_authorization("u","")); svc_module._pending_authorizations[st["auth_id"]]["latest_check"] = 0; _,res=asyncio.run(svc.check_authorization("u",st["auth_id"],st["device_code"])); assert res["status"]=="declined"

def test_regression_source_does_not_use_token_verification():
    assert "copilot/token_verification" not in Path("app/services/copilot_auth_service.py").read_text()


def test_start_handles_non_json_error_response(monkeypatch):
    calls=[]
    class _RespErr(_Resp):
        def __init__(self):
            self.status_code=500; self.text="<html>boom</html>"; self.content=b"<html>boom</html>"
        def json(self):
            raise ValueError("not json")
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,lambda *_:_RespErr()))
    status, payload = asyncio.run(CopilotAuthService().start_authorization("u", ""))
    assert status == 502
    assert "details" in payload
    assert "gho_" not in str(payload)

def test_check_handles_non_json_error_response(monkeypatch):
    calls=[]
    class _RespErr(_Resp):
        def __init__(self):
            self.status_code=500; self.text="<html>boom</html>"; self.content=b"<html>boom</html>"
        def json(self):
            raise ValueError("not json")
    def factory(_u,_h,j):
        if j.get("scope"):
            return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _RespErr()
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory))
    svc = CopilotAuthService(); _, st = asyncio.run(svc.start_authorization("u", "")); svc_module._pending_authorizations[st["auth_id"]]["latest_check"] = 0
    status, payload = asyncio.run(svc.check_authorization("u", st["auth_id"], st["device_code"]))
    assert status == 200
    assert payload["status"] == "failed"
    assert isinstance(payload.get("message"), str)


def test_check_invalid_auth_id_returns_404():
    status, payload = asyncio.run(CopilotAuthService().check_authorization("u", "missing", "device"))
    assert status == 404
    assert "not found" in payload["error"].lower()

def test_authorized_response_contains_oauth_summary(monkeypatch):
    calls=[]
    def factory(_u,_h,j):
        if j.get("scope"):
            return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _Resp(200,{"access_token":"gho_SECRET1234","token_type":"bearer","scope":"read:user"})
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory))
    svc=CopilotAuthService(); _,st=asyncio.run(svc.start_authorization("u",""))
    svc_module._pending_authorizations[st["auth_id"]]["latest_check"] = 0
    _,res=asyncio.run(svc.check_authorization("u",st["auth_id"],st["device_code"]))
    summary = res["oauth_summary"]
    assert summary["token_prefix"] == "gho_"
    assert summary["token_suffix"] == "1234"
    assert summary["token_length"] == len("gho_SECRET1234")
    assert "access" not in summary and "refresh" not in summary


def test_oauth_summary_does_not_expose_short_token():
    summary = CopilotAuthService._oauth_summary(
        {"type": "oauth", "access": "short", "refresh": "short", "expires": 0},
        "opencode",
    )
    dumped = json.dumps(summary)
    assert "short" not in dumped
    assert summary["token_prefix"] == ""
    assert summary["token_suffix"] == ""
    assert summary["token_length"] == len("short")

def test_check_slow_down_updates_latest_check_and_rate_limits_next_poll(monkeypatch):
    calls = []

    def factory(_url, _headers, payload):
        if payload.get("scope"):
            return _Resp(
                200,
                {
                    "device_code": "d",
                    "user_code": "u",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900,
                    "interval": 5,
                },
            )
        return _Resp(200, {"error": "slow_down"})

    monkeypatch.setattr(
        svc_module.httpx,
        "AsyncClient",
        lambda *a, **k: _Client(calls, factory),
    )

    svc = CopilotAuthService()
    _, started = asyncio.run(svc.start_authorization("u", ""))
    auth_id = started["auth_id"]
    svc_module._pending_authorizations[auth_id]["latest_check"] = 0

    _, first = asyncio.run(
        svc.check_authorization("u", auth_id, started["device_code"])
    )
    assert first["status"] == "pending"
    assert first["interval"] >= 10
    assert svc_module._pending_authorizations[auth_id]["latest_check"] > 0

    token_calls_after_first = [
        call for call in calls
        if call["json"].get("grant_type") == "urn:ietf:params:oauth:grant-type:device_code"
    ]

    _, second = asyncio.run(
        svc.check_authorization("u", auth_id, started["device_code"])
    )
    assert second["status"] == "pending"

    token_calls_after_second = [
        call for call in calls
        if call["json"].get("grant_type") == "urn:ietf:params:oauth:grant-type:device_code"
    ]

    assert len(token_calls_after_second) == len(token_calls_after_first)


def test_check_authorization_omits_enterprise_url_for_enterprise_base_input(monkeypatch):
    calls=[]
    def factory(_u,_h,j):
        if j.get("scope"): return _Resp(200,{"device_code":"d","user_code":"u","verification_uri":"https://github.com/login/device","expires_in":900,"interval":5})
        return _Resp(200,{"access_token":"gho_ENTERPRISE_IGNORED","token_type":"bearer","scope":"read:user"})
    monkeypatch.setattr(svc_module.httpx,"AsyncClient",lambda *a,**k:_Client(calls,factory))
    svc=CopilotAuthService(); _,st=asyncio.run(svc.start_authorization("u","https://github.company.com/api/v3")); svc_module._pending_authorizations[st["auth_id"]]["latest_check"] = 0
    _,res=asyncio.run(svc.check_authorization("u",st["auth_id"],st["device_code"]))
    oauth = res["oauth"]
    assert oauth["access"] == "gho_ENTERPRISE_IGNORED"
    assert oauth["refresh"] == "gho_ENTERPRISE_IGNORED"
    assert oauth["type"] == "oauth"
    assert oauth["expires"] == 0
    assert "enterpriseUrl" not in oauth
