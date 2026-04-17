import boto3
import argparse
from dotenv import load_dotenv

load_dotenv()

s3 = boto3.client('s3')

parser = argparse.ArgumentParser()
parser.add_argument('bucket_name', type=str)
args = parser.parse_args()

bucket = args.bucket_name

buckets = [b['Name'] for b in s3.list_buckets()['Buckets']]

if bucket not in buckets:
    print(f"Bucket '{bucket}' does not exist.")
else:
    objects = s3.list_objects_v2(Bucket=bucket)
    if 'Contents' in objects:
        for obj in objects['Contents']:
            s3.delete_object(Bucket=bucket, Key=obj['Key'])
            print(f"Deleted object: {obj['Key']}")

    s3.delete_bucket(Bucket=bucket)
    print(f"Bucket '{bucket}' deleted")