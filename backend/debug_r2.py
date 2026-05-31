"""Standalone R2 credential diagnostic.

Run on Render's shell OR locally with the same env vars you set on Render:

    R2_BUCKET=... R2_ENDPOINT=... R2_ACCESS_KEY_ID=... \
    R2_SECRET_ACCESS_KEY=... python debug_r2.py

It surfaces the underlying error code that boto3 swallows behind generic
"Unauthorized", and reports lengths + first/last 4 chars of each env var
so trailing whitespace / paste typos jump out.
"""
import hashlib
import os
import sys


def _safe_show(label: str, value: str | None) -> None:
    if not value:
        print(f"  {label:30s} = <UNSET>")
        return
    has_ws = value != value.strip()
    length = len(value)
    head = value[:4] if length >= 4 else value
    tail = value[-4:] if length >= 8 else ""
    sha8 = hashlib.sha256(value.encode()).hexdigest()[:8]
    flags = " <-- HAS LEADING/TRAILING WHITESPACE!" if has_ws else ""
    print(f"  {label:30s} = len={length}  head={head!r}  tail={tail!r}  sha8={sha8}{flags}")


def main() -> int:
    bucket = os.environ.get("R2_BUCKET")
    endpoint = os.environ.get("R2_ENDPOINT")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")

    print("== Env vars (showing length, head, tail, sha) ==")
    _safe_show("R2_BUCKET", bucket)
    _safe_show("R2_ENDPOINT", endpoint)
    _safe_show("R2_ACCESS_KEY_ID", access_key)
    _safe_show("R2_SECRET_ACCESS_KEY", secret_key)
    print()

    if not all([bucket, endpoint, access_key, secret_key]):
        print("FAIL: one or more env vars are missing")
        return 1

    # Expected R2 shape: access key id is exactly 32 hex chars, secret is 64
    # hex chars. If yours are different, you may have grabbed the wrong field.
    print("== Sanity checks ==")
    if len(access_key) != 32:
        print(f"  WARN: R2 access keys are usually 32 chars; yours is {len(access_key)}")
    else:
        print("  OK: access key length = 32")
    if len(secret_key) != 64:
        print(f"  WARN: R2 secret keys are usually 64 chars; yours is {len(secret_key)}")
    else:
        print("  OK: secret key length = 64")
    if not endpoint.startswith("https://") or not endpoint.endswith(".r2.cloudflarestorage.com"):
        print(f"  WARN: endpoint doesn't look like https://<account>.r2.cloudflarestorage.com")
    else:
        print("  OK: endpoint shape looks right")
    print()

    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", retries={"max_attempts": 1}),
        region_name="auto",
    )

    def _try(label: str, fn):
        print(f"== {label} ==")
        try:
            result = fn()
            print(f"  OK -> {result}")
            return True
        except ClientError as e:
            err = e.response.get("Error", {})
            print(f"  FAIL: code={err.get('Code')!r}  message={err.get('Message')!r}")
            print(f"  HTTP status = {e.response.get('ResponseMetadata', {}).get('HTTPStatusCode')}")
            print(f"  Request ID  = {e.response.get('ResponseMetadata', {}).get('RequestId')}")
            return False
        except Exception as e:
            print(f"  FAIL ({type(e).__name__}): {e}")
            return False
        finally:
            print()

    # Test 1: list buckets — needs Account-level perms; not all R2 tokens
    # have this. Failure is informative but non-fatal.
    _try("list_buckets (account-level perm — may fail even with valid tokens)",
         lambda: [b["Name"] for b in client.list_buckets().get("Buckets", [])])

    # Test 2: head_bucket — needs the bucket to exist and the token to have
    # access. If this fails with 403, the token is scoped wrong or read-only.
    # If it fails with 404, the bucket name doesn't exist.
    ok = _try(f"head_bucket({bucket})",
              lambda: client.head_bucket(Bucket=bucket) or "exists + accessible")
    if not ok:
        print("Stop here — head_bucket failed. Read the error code above:")
        print("  403 / AccessDenied        -> token lacks access OR is Read-only")
        print("  403 / SignatureDoesNotMatch -> secret access key is wrong")
        print("  401 / InvalidAccessKeyId  -> access key id is wrong / deleted")
        print("  404 / NoSuchBucket        -> bucket name doesn't match Cloudflare")
        return 1

    # Test 3: tiny write + read + delete
    test_key = "debug-r2/_probe.txt"
    payload = b"r2-debug-probe"
    _try(f"put_object({test_key})",
         lambda: client.put_object(Bucket=bucket, Key=test_key, Body=payload) and "wrote")
    _try(f"get_object({test_key})",
         lambda: client.get_object(Bucket=bucket, Key=test_key)["Body"].read() == payload)
    _try(f"delete_object({test_key})",
         lambda: client.delete_object(Bucket=bucket, Key=test_key) and "deleted")

    print("If put_object succeeded above, your R2 creds are good. The issue")
    print("on Render is then either env-var whitespace OR a stale deploy that")
    print("hasn't picked up the updated values yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
