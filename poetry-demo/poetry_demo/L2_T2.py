import boto3
import argparse
import json
from dotenv import load_dotenv

load_dotenv()

s3 = boto3.client('s3')

parser = argparse.ArgumentParser()
parser.add_argument('bucket_name', type=str)
args = parser.parse_args()

bucket = args.bucket_name
try:
    s3.get_bucket_policy(Bucket=bucket)
    print(f"Policy already exists IN '{bucket}'.")

except s3.exceptions.from_code('NoSuchBucketPolicy'):
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": [
                    f"arn:aws:s3:::{bucket}/dev/*",
                    f"arn:aws:s3:::{bucket}/test/*"
                ]
            }
        ]
    }

    s3.put_bucket_policy(
        Bucket=bucket,
        Policy=json.dumps(policy)
    )
    print(f"Policy created!")