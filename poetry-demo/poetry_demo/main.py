import boto3
from dotenv import load_dotenv

load_dotenv()

# Connect to S3
s3 = boto3.client('s3')

# List all your buckets
response = s3.list_buckets()
for bucket in response['Buckets']:
    print(bucket['Name'])