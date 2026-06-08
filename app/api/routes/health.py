from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/healthz", include_in_schema=False)
async def health():
    return {"status": "ok", "service": "novel-localization-ai", "version": "1.0.0"}
