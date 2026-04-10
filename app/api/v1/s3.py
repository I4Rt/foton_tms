from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from app.core.security import get_current_user
from app.models.models import User
from app.models.enums import ImageSize
from app.schemas.schemas import UploadedImageResponse, DeleteImageResponse, DeleteImageRequest
from app.core.s3client import S3Client, get_s3_client
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.enums import UserRole

from io import BytesIO
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image, ImageOps
from botocore.exceptions import ClientError

from app.core.logging import logger

router = APIRouter(tags=["s3"])


def _get_max_image_size(size: ImageSize) -> int:
    if size == ImageSize.s:
        return 256
    if size == ImageSize.m:
        return 480
    return 1280

from urllib.parse import urlparse, unquote


def _extract_s3_key_from_url(url: str, bucket_name: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path)

    prefix = f"/{bucket_name}/"
    if not path.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL does not belong to the configured bucket",
        )

    key = path[len(prefix):]
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Object key is empty",
        )

    return key


@router.post(
    "/images",
    response_model=UploadedImageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_image(
    size: ImageSize,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR, UserRole.EXECUTOR)),
    s3_client: S3Client = Depends(get_s3_client),
):

    """Upload image to S3 with proportional resize."""
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported image type",
        )

    try:
        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty file",
            )

        image = Image.open(BytesIO(raw_bytes))
        image = ImageOps.exif_transpose(image)

        max_size = _get_max_image_size(size)
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        buffer = BytesIO()

        if file.content_type == "image/png":
            ext = "png"
            content_type = "image/png"
            image.save(buffer, format="PNG", optimize=True)

        elif file.content_type == "image/webp":
            ext = "webp"
            content_type = "image/webp"
            image.save(buffer, format="WEBP", quality=90, method=6)

        else:
            ext = "jpg"
            content_type = "image/jpeg"
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(buffer, format="JPEG", quality=90, optimize=True)

        buffer.seek(0)

        key = f"images/{current_user.id}/{size.value}/{uuid4()}.{ext}"

        s3_client.put_bytes(
            key=key,
            data=buffer.getvalue(),
            content_type=content_type,
        )

        width, height = image.size
        url = s3_client.build_object_url(key)

        return UploadedImageResponse(
            url=url,
            key=key,
            width=width,
            height=height,
            size=size,
        )

    except HTTPException:
        raise
    except ClientError as e:
        logger.exception("S3 upload failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to upload image to storage",
        )
    except Exception as e:
        logger.exception("Image upload failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image file",
        )
    
@router.delete(
    "/images",
    response_model=DeleteImageResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_image(
    payload: DeleteImageRequest,
    current_user: User = Depends(
        require_role(
            UserRole.MANAGER,
            UserRole.ADMINISTRATOR,
            UserRole.EXECUTOR,
        )
    ),
    s3_client: S3Client = Depends(get_s3_client),
):
    try:
        key = _extract_s3_key_from_url(
            url=str(payload.url),
            bucket_name=s3_client.bucket_name,
        )

        s3_client.delete(key)

        return DeleteImageResponse(
            deleted=True,
            key=key,
        )

    except HTTPException:
        raise
    except ClientError as e:
        logger.exception("S3 delete failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete image from storage",
        )