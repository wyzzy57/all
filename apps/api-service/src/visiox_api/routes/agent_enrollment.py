from collections.abc import Generator
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from visiox_api.schemas.agent_protocol import EnrollmentRequest, EnrollmentResponse
from visiox_api.services.agent_identity import AgentIdentityError
from visiox_api.services.management_proxy import require_management_proxy
from visiox_api.services.node_registry import EnrollmentRejected, NodeRegistryService
from visiox_common.settings import Settings, get_settings
from visiox_db.session import get_session


router = APIRouter(prefix="/agent/v1", tags=["agent-enrollment"])


class EnrollmentTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)


class EnrollmentTokenResponse(BaseModel):
    id: str
    name: str
    token: str
    expires_at: datetime


def get_agent_enrollment_session() -> Generator[Session]:
    yield from get_session()


@router.post(
    "/enrollment-tokens",
    response_model=EnrollmentTokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_management_proxy)],
)
def create_enrollment_token(
    request: EnrollmentTokenRequest,
    session: Session = Depends(get_agent_enrollment_session),
    settings: Settings = Depends(get_settings),
) -> EnrollmentTokenResponse:
    created = NodeRegistryService(session, settings).create_enrollment_token(request.name)
    return EnrollmentTokenResponse(
        id=created.id,
        name=created.name,
        token=created.token,
        expires_at=created.expires_at,
    )


@router.post("/enroll", response_model=EnrollmentResponse, status_code=status.HTTP_201_CREATED)
def enroll_agent(
    request: EnrollmentRequest,
    session: Session = Depends(get_agent_enrollment_session),
    settings: Settings = Depends(get_settings),
) -> EnrollmentResponse:
    try:
        result = NodeRegistryService(session, settings).enroll(request)
    except EnrollmentRejected:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Enrollment rejected") from None
    except AgentIdentityError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Certificate request is invalid",
        ) from None
    return EnrollmentResponse(
        enrollment_request_id=result.enrollment_request_id,
        node_id=result.node.id,
        certificate_pem=result.certificate.certificate_pem,
        ca_certificate_pem=result.certificate.ca_certificate_pem,
        gateway_url=result.gateway_url,
        heartbeat_interval_seconds=result.heartbeat_interval_seconds,
    )
