import os

import httpx
import jwt
from fastapi import HTTPException, status
from app.repositories.user_repo import UserRepository
from app.repositories.audit_repo import AuditRepository
from app.db import SessionLocal
from app.services.auth_service import hash_password, issue_session_token
import random

async def get_user_by_code(redirect_uri: str, code: str):
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"https://sso-auth.company.com/realms/persons/protocol/openid-connect/token",
                data={
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                    "client_id": "webapp",
                    "scope": "offline_access",
                    "code": code
                }
            )
            token = response.json()
            tokens = jwt.decode(token['access_token'], options={"verify_signature": False})

            # import json
            # print(json.dumps(tokens, indent=2))
            if 'preferred_username' in tokens and tokens['preferred_username'] != '':
                session_name = tokens['preferred_username']
            elif 'email' in tokens and tokens['email'] != '':
                session_name = tokens['email']
            else:
                session_name = 'someone'
            return session_name, tokens['email']
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def login_user_by_code(redirect_uri: str, code: str):
    username, email = await get_user_by_code(redirect_uri, code)
    display_name = " ".join(part.capitalize() for part in email.split("@",1)[0].split(".") if part)

    db = SessionLocal()
    repo = UserRepository(db)
    user = repo.get_by_username(username)
    if not user:
        try:
            user = repo.create(username, hash_password(f"{username}_{random.randint(1, 99999)}"), "user", display_name)
        except Exception as e:
            db.rollback()
            # Check if it's a duplicate key error
            if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
            raise

        # Audit trail for registration
        AuditRepository(db).create(
            action="register",
            target_type="user",
            target_id=str(user.id),
            user_id=user.id,
            details={"username": user.username},
        )

    print(f"login user (id: {user.id}, username: {user.username}, nickname: {display_name})")

    # Auto-login
    return issue_session_token(user.id)

