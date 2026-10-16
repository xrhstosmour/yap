"""The files a domain serves so the platforms trust its native apps.

Both answer 404 until the apps are configured, see `APPLE_APP_IDS`,
`ANDROID_APP_PACKAGE` and `ANDROID_CERT_FINGERPRINTS`. Passkeys are always
delegated, links only for the paths in `APP_LINK_PATHS`, since a link
opening the app needs routes the app handles.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import status
from fastapi.responses import JSONResponse

from app.core.settings import settings

router = APIRouter(prefix="/.well-known", include_in_schema=False)


@router.get("/apple-app-site-association")
async def apple_app_site_association() -> JSONResponse:
    """Let the iOS apps use this domain's passkeys.

    Served as JSON at this exact path without a redirect, which is the
    only shape Apple's fetcher accepts.
    """
    if not settings.APPLE_APP_IDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    association: dict[str, Any] = {"webcredentials": {"apps": settings.APPLE_APP_IDS}}
    if settings.APP_LINK_PATHS:
        association["applinks"] = {
            "details": [
                {
                    "appIDs": settings.APPLE_APP_IDS,
                    "components": [{"/": path} for path in settings.APP_LINK_PATHS],
                }
            ]
        }
    return JSONResponse(association)


@router.get("/assetlinks.json")
async def asset_links() -> JSONResponse:
    """Let the Android app use this domain's passkeys."""
    if not settings.ANDROID_APP_PACKAGE or not settings.ANDROID_CERT_FINGERPRINTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # Android checks the host here and the paths in the app's own manifest.
    relations = ["delegate_permission/common.get_login_creds"]
    if settings.APP_LINK_PATHS:
        relations.append("delegate_permission/common.handle_all_urls")
    return JSONResponse(
        [
            {
                "relation": relations,
                "target": {
                    "namespace": "android_app",
                    "package_name": settings.ANDROID_APP_PACKAGE,
                    "sha256_cert_fingerprints": settings.ANDROID_CERT_FINGERPRINTS,
                },
            }
        ]
    )
