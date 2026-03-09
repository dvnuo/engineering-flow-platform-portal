import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import uuid
from datetime import datetime

router = APIRouter(prefix="/api/copilot/auth", tags=["copilot"])

# In-memory store for pending authorizations
_pending_authorizations = {}


class AuthStartResponse(BaseModel):
    auth_id: str
    device_code: str
    user_code: str
    verification_url: str
    verification_complete_url: str
    expires_in: int
    interval: int


class AuthCheckRequest(BaseModel):
    auth_id: str
    device_code: str


class AuthCheckResponse(BaseModel):
    status: str
    message: str = ""
    token: str = ""


@router.post("/start")
async def start_auth(request: Request):
    """Start GitHub Copilot device authorization."""
    try:
        # Get GitHub base URL from config (need to fetch from agent's config)
        # For now, use default GitHub API
        api_base_url = "https://api.github.com"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{api_base_url}/copilot/token_verification",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"action": "create"}
            )
            
            if response.status_code != 201:
                return {
                    "error": f"GitHub API returned {response.status_code}",
                    "details": response.text
                }
            
            data = response.json()
            
            auth_id = str(uuid.uuid4())[:8]
            device_code = data.get("device_code", str(uuid.uuid4()))
            
            _pending_authorizations[auth_id] = {
                "device_code": device_code,
                "user_code": data.get("user_code", ""),
                "verification_uri": data.get("verification_uri", ""),
                "verification_uri_complete": data.get("verification_uri_complete", ""),
                "expires_at": datetime.now().timestamp() + data.get("expires_in", 600),
                "interval": data.get("interval", 5),
                "status": "pending",
                "token": None,
                "created_at": datetime.now().isoformat(),
            }
            
            return AuthStartResponse(
                auth_id=auth_id,
                device_code=device_code,
                user_code=data.get("user_code", ""),
                verification_url=data.get("verification_uri", ""),
                verification_complete_url=data.get("verification_uri_complete", ""),
                expires_in=data.get("expires_in", 600),
                interval=data.get("interval", 5),
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/check")
async def check_auth(request: AuthCheckRequest):
    """Check if authorization is complete."""
    auth = _pending_authorizations.get(request.auth_id)
    
    if not auth:
        return AuthCheckResponse(status="expired", message="Authorization not found or expired")
    
    # Check if expired
    if datetime.now().timestamp() > auth["expires_at"]:
        _pending_authorizations.pop(request.auth_id, None)
        return AuthCheckResponse(status="expired", message="Authorization expired")
    
    # Check if already authorized
    if auth.get("token"):
        return AuthCheckResponse(status="authorized", token=auth["token"])
    
    try:
        api_base_url = "https://api.github.com"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{api_base_url}/copilot/token_verification",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "action": "complete",
                    "device_code": request.device_code,
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                token = data.get("token", "")
                auth["token"] = token
                auth["status"] = "authorized"
                return AuthCheckResponse(status="authorized", token=token)
            elif response.status_code == 202:
                return AuthCheckResponse(status="pending", message="Still waiting for authorization...")
            else:
                return AuthCheckResponse(status="failed", message=f"GitHub API error: {response.status_code}")
                
    except Exception as e:
        return AuthCheckResponse(status="failed", message=str(e))
