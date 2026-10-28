"""Opt-in local candidate profile verification. Never enable in deployment."""

from ipaddress import ip_address

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.services.candidate_profile import CandidateProfile
from app.services.sessions import InMemorySessionStore


class ProfileInspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str = Field(min_length=1, max_length=128)


async def require_loopback(request: Request) -> None:
    # Reject forwarded requests even when Uvicorn has rewritten request.client.
    forwarded = any(
        name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")
        for name in request.headers
    )
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if forwarded or not local:
        raise HTTPException(status_code=403, detail="Local access only.")


def create_dev_profile_router(sessions: InMemorySessionStore) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_loopback)])

    @router.post(
        "/api/v1/dev/candidate-profile", response_model=CandidateProfile,
        include_in_schema=False,
    )
    async def inspect_profile(body: ProfileInspectionRequest, response: Response) -> CandidateProfile:
        session = await sessions.resolve(body.session_id, workflow="candidate_intake")
        profile = await sessions.candidate_profile_for(session)
        response.headers["Cache-Control"] = "no-store"
        return profile

    return router
