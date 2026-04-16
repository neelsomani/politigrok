from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import boto3
import botocore


class StorageBackend:
    def __init__(
        self,
        mode: str,
        bucket: Optional[str] = None,
        region: Optional[str] = None,
        local_dir: str = "data",
    ) -> None:
        self.mode = mode
        self.bucket = bucket
        self.local_dir = Path(local_dir)

        if self.mode == "s3":
            session = boto3.session.Session(region_name=region)
            self.s3_client = session.client("s3")
        else:
            self.local_dir.mkdir(parents=True, exist_ok=True)

    def _local_path_for_key(self, key: str) -> Path:
        return self.local_dir / key

    def exists(self, key: str) -> bool:
        if self.mode == "s3":
            try:
                self.s3_client.head_object(Bucket=self.bucket, Key=key)
                return True
            except botocore.exceptions.ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in {"404", "NoSuchKey", "NotFound"}:
                    return False
                raise

        return self._local_path_for_key(key).exists()

    def put_json(self, key: str, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        if self.mode == "s3":
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
            return

        path = self._local_path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def get_json(self, key: str) -> dict:
        if self.mode == "s3":
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            return json.loads(response["Body"].read().decode("utf-8"))

        path = self._local_path_for_key(key)
        return json.loads(path.read_text(encoding="utf-8"))

    def list_json_keys(self, key_prefix: str) -> List[str]:
        if self.mode == "s3":
            continuation_token = None
            keys = []

            while True:
                kwargs = {
                    "Bucket": self.bucket,
                    "Prefix": key_prefix,
                    "MaxKeys": 1000,
                }
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token

                response = self.s3_client.list_objects_v2(**kwargs)
                for item in response.get("Contents", []):
                    key = item["Key"]
                    if key.endswith(".json"):
                        keys.append(key)

                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")

            return sorted(keys)

        root = self._local_path_for_key(key_prefix)
        if not root.exists():
            return []

        return sorted(
            str(path.relative_to(self.local_dir))
            for path in root.rglob("*.json")
            if path.is_file()
        )


def choose_mode(mode: str, bucket: Optional[str]) -> str:
    if mode not in {"auto", "s3", "local"}:
        raise ValueError("mode must be one of: auto, s3, local")
    if mode == "auto":
        return "s3" if bucket else "local"
    return mode
