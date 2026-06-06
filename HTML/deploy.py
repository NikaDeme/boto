
import argparse
import io
import json
import os
import sys
import time
import zipfile
import boto3
from botocore.exceptions import ClientError

# ── helpers ────────────────────────────────────────────────────────────────────

def log(msg): print(f"  {msg}")
def ok(msg):  print(f"  [OK] {msg}")
def err(msg): print(f"  [ERROR] {msg}"); sys.exit(1)


def make_zip(src_path: str) -> bytes:
    """Zip a single Python file in-memory and return bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(src_path, arcname="lambda_function.py")
    return buf.getvalue()


# ── deploy ─────────────────────────────────────────────────────────────────────

def deploy(args):
    region = args.region
    prefix = args.prefix
    hf_token = args.hf_token

    bucket_name = f"{prefix}-images-{region}"
    table_name  = f"{prefix}-predictions"
    role_name   = f"{prefix}-lambda-role"
    func_name   = f"{prefix}-analyser"
    api_name    = f"{prefix}-api"

    s3_client  = boto3.client("s3",          region_name=region)
    ddb        = boto3.client("dynamodb",     region_name=region)
    iam        = boto3.client("iam")
    lam        = boto3.client("lambda",       region_name=region)
    apigw      = boto3.client("apigatewayv2", region_name=region)
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    # ── 1. S3 Bucket ────────────────────────────────────────────────────────
    print("\n[1/5] Creating S3 bucket...")
    try:
        if region == "us-east-1":
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        # Block all public access
        s3_client.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True, "IgnorePublicAcls": True,
                "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
            },
        )
        ok(f"S3 bucket created: {bucket_name}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            ok(f"S3 bucket already exists: {bucket_name}")
        else:
            err(f"S3 error ({code}): {e.response['Error']['Message']}")

    # ── 2. DynamoDB table ────────────────────────────────────────────────────
    print("\n[2/5] Creating DynamoDB table...")
    try:
        ddb.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # Wait until active
        waiter = ddb.get_waiter("table_exists")
        waiter.wait(TableName=table_name)
        ok(f"DynamoDB table created: {table_name}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceInUseException":
            ok(f"DynamoDB table already exists: {table_name}")
        else:
            err(f"DynamoDB error ({code}): {e.response['Error']['Message']}")

    # ── 3. IAM role ──────────────────────────────────────────────────────────
    print("\n[3/5] Creating IAM role...")
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
    try:
        role_resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="VisionLab Lambda execution role",
        )
        role_arn = role_resp["Role"]["Arn"]
        ok(f"IAM role created: {role_arn}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
            ok(f"IAM role already exists: {role_arn}")
        else:
            err(f"IAM error: {e.response['Error']['Message']}")

    # Attach managed policies
    for policy_arn in [
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/AmazonS3FullAccess",
        "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess",
    ]:
        try:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        except ClientError:
            pass  # already attached
    log("IAM policies attached. Waiting 10 s for propagation...")
    time.sleep(10)

    # ── 4. Lambda function ───────────────────────────────────────────────────
    print("\n[4/5] Deploying Lambda function...")
    lambda_src = os.path.join(os.path.dirname(__file__), "lambda_function.py")
    zip_bytes  = make_zip(lambda_src)
    env_vars   = {
        "HF_API_TOKEN":   hf_token,
        "S3_BUCKET_NAME": bucket_name,
        "DYNAMODB_TABLE": table_name,
    }

    try:
        func_resp = lam.create_function(
            FunctionName=func_name,
            Runtime="python3.12",
            Role=role_arn,
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": zip_bytes},
            Timeout=60,
            MemorySize=256,
            Environment={"Variables": env_vars},
        )
        func_arn = func_resp["FunctionArn"]
        ok(f"Lambda function created: {func_arn}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceConflictException":
            # Update existing function
            lam.update_function_code(FunctionName=func_name, ZipFile=zip_bytes)
            lam.update_function_configuration(
                FunctionName=func_name,
                Environment={"Variables": env_vars},
            )
            func_arn = lam.get_function(FunctionName=func_name)["Configuration"]["FunctionArn"]
            ok(f"Lambda function updated: {func_arn}")
        else:
            err(f"Lambda error ({code}): {e.response['Error']['Message']}")

    # Wait for function to be active
    waiter = lam.get_waiter("function_active_v2")
    waiter.wait(FunctionName=func_name)

    # ── 5. API Gateway ───────────────────────────────────────────────────────
    print("\n[5/5] Creating API Gateway HTTP API...")
    try:
        api_resp = apigw.create_api(
            Name=api_name,
            ProtocolType="HTTP",
            CorsConfiguration={
                "AllowOrigins": ["*"],
                "AllowMethods": ["POST", "OPTIONS"],
                "AllowHeaders": ["Content-Type"],
            },
        )
        api_id  = api_resp["ApiId"]
        api_url = api_resp["ApiEndpoint"]
        ok(f"HTTP API created: {api_id}")

        # Lambda integration
        integ_resp = apigw.create_integration(
            ApiId=api_id,
            IntegrationType="AWS_PROXY",
            IntegrationUri=func_arn,
            PayloadFormatVersion="2.0",
        )
        integ_id = integ_resp["IntegrationId"]

        # Route
        apigw.create_route(
            ApiId=api_id,
            RouteKey="POST /analyse",
            Target=f"integrations/{integ_id}",
        )

        # Stage (auto-deploy)
        apigw.create_stage(
            ApiId=api_id,
            StageName="prod",
            AutoDeploy=True,
        )

        # Allow API GW to invoke Lambda
        lam.add_permission(
            FunctionName=func_name,
            StatementId="apigateway-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{region}:{account_id}:{api_id}/*",
        )

        invoke_url = f"{api_url}/prod/analyse"
        ok(f"API endpoint ready: {invoke_url}")

    except ClientError as e:
        err(f"API Gateway error: {e.response['Error']['Message']}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  DEPLOYMENT COMPLETE")
    print("═" * 60)
    print(f"  S3 Bucket   : {bucket_name}")
    print(f"  DynamoDB    : {table_name}")
    print(f"  Lambda      : {func_name}")
    print(f"  API URL     : {invoke_url}")
    print()
    print("  → Copy the API URL into frontend/app.js → API_ENDPOINT")
    print("═" * 60 + "\n")


# ── destroy ────────────────────────────────────────────────────────────────────

def destroy(args):
    region = args.region
    prefix = args.prefix

    bucket_name = f"{prefix}-images-{region}"
    table_name  = f"{prefix}-predictions"
    role_name   = f"{prefix}-lambda-role"
    func_name   = f"{prefix}-analyser"

    s3_client = boto3.client("s3",      region_name=region)
    ddb       = boto3.client("dynamodb",region_name=region)
    iam       = boto3.client("iam")
    lam       = boto3.client("lambda",  region_name=region)
    apigw     = boto3.client("apigatewayv2", region_name=region)

    print("\nDestroying all resources...")

    # API GW
    try:
        apis = apigw.get_apis()["Items"]
        for api in apis:
            if api["Name"] == f"{prefix}-api":
                apigw.delete_api(ApiId=api["ApiId"])
                log(f"API Gateway deleted: {api['ApiId']}")
    except ClientError as e:
        log(f"API GW delete skipped: {e.response['Error']['Message']}")

    # Lambda
    try:
        lam.delete_function(FunctionName=func_name)
        log(f"Lambda deleted: {func_name}")
    except ClientError as e:
        log(f"Lambda delete skipped: {e.response['Error']['Message']}")

    # IAM role
    for policy_arn in [
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/AmazonS3FullAccess",
        "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess",
    ]:
        try:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        except ClientError:
            pass
    try:
        iam.delete_role(RoleName=role_name)
        log(f"IAM role deleted: {role_name}")
    except ClientError as e:
        log(f"IAM role delete skipped: {e.response['Error']['Message']}")

    # Empty & delete S3 bucket
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name):
            objects = page.get("Contents", [])
            if objects:
                s3_client.delete_objects(
                    Bucket=bucket_name,
                    Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
                )
        s3_client.delete_bucket(Bucket=bucket_name)
        log(f"S3 bucket deleted: {bucket_name}")
    except ClientError as e:
        log(f"S3 delete skipped: {e.response['Error']['Message']}")

    # DynamoDB
    try:
        ddb.delete_table(TableName=table_name)
        log(f"DynamoDB table deleted: {table_name}")
    except ClientError as e:
        log(f"DynamoDB delete skipped: {e.response['Error']['Message']}")

    print("\nDone. All resources destroyed.\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="VisionLab AWS deployer")
    parser.add_argument("--region",  default="us-east-1", help="AWS region")
    parser.add_argument("--prefix",  default="visionlab", help="Resource name prefix")
    parser.add_argument("--hf-token", default="", help="HuggingFace API token (required for deploy)")
    parser.add_argument("--destroy", action="store_true", help="Tear down all resources")
    args = parser.parse_args()

    if args.destroy:
        destroy(args)
    else:
        if not args.hf_token:
            print("[ERROR] --hf-token is required for deployment.")
            sys.exit(1)
        deploy(args)


if __name__ == "__main__":
    main()
