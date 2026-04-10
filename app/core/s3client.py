import boto3
from app.core.config import get_settings

settings = get_settings()


class S3Client:
    def __init__(
        self,
        bucket_name: str = settings.S3_BUCKET_NAME,
        aws_access_key_id: str = settings.S3_IDENTIFYER,
        aws_secret_access_key: str = settings.S3_KEY,
        endpoint_url: str = "https://storage.yandexcloud.net",
        region_name: str = "ru-central1",
    ) -> None:
        self.bucket_name = bucket_name
        self.endpoint_url = endpoint_url.rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,     # ВАЖНО
            region_name=region_name,       # ru-central1
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        params = {
            "Bucket": self.bucket_name,
            "Key": key,
            "Body": data,
        }
        if content_type is not None:
            params["ContentType"] = content_type

        self.client.put_object(**params)

    def build_object_url(self, key: str) -> str:
        return f"{self.endpoint_url}/{self.bucket_name}/{key}"

    def get_bytes(self, key: str) -> bytes:
        response = self.client.get_object(
            Bucket=self.bucket_name,
            Key=key,
        )
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(
            Bucket=self.bucket_name,
            Key=key,
        )

    def list_keys(self, prefix: str = "") -> list[str]:
        response = self.client.list_objects_v2(
            Bucket=self.bucket_name,
            Prefix=prefix,
        )
        return [obj["Key"] for obj in response.get("Contents", [])]
    

    def build_object_url(self, key: str) -> str:
        return f"{self.endpoint_url}/{self.bucket_name}/{key}"

s3_client = S3Client()

def get_s3_client():
    return s3_client