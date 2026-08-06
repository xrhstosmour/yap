"""API v1 router combining all endpoints.

This module combines all v1 API endpoints into a single router.
"""

from fastapi import APIRouter

from app.api.v1.api_keys import router as api_keys_router
from app.api.v1.auth import router as auth_router
from app.api.v1.billing import router as billing_router
from app.api.v1.billing_webhooks import router as billing_webhooks_router
from app.api.v1.feature_flags import router as feature_flags_router
from app.api.v1.files import router as files_router
from app.api.v1.health import router as health_router
from app.api.v1.tenants import router as tenants_router
from app.api.v1.users import router as users_router
from app.api.v1.websocket import router as websocket_router

router = APIRouter(prefix="/api/v1")

router.include_router(auth_router)
router.include_router(users_router)
router.include_router(api_keys_router)
router.include_router(files_router)
router.include_router(tenants_router)
router.include_router(health_router)
router.include_router(feature_flags_router)
router.include_router(websocket_router)
router.include_router(billing_router)
router.include_router(billing_webhooks_router)
