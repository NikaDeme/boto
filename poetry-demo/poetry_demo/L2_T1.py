import boto3
import argparse
from dotenv import load_dotenv

load_dotenv()

s3 = boto3.client('s3')

parser = argparse.ArgumentParser()
parser.add_argument('bucket_name', type=str)
args = parser.parse_args()

buckets = [b['Name'] for b in s3.list_buckets()['Buckets']]

if args.bucket_name in buckets:
    print(f"Bucket '{args.bucket_name}' already exists.")
else:
    s3.create_bucket(Bucket=args.bucket_name)
    print(f"Bucket '{args.bucket_name}' created successfully!")