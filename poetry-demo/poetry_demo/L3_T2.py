import boto3
import argparse
from dotenv import load_dotenv

load_dotenv()

s3 = boto3.client('s3')

parser = argparse.ArgumentParser()
parser.add_argument('bucket_name', type=str)
parser.add_argument('file_name', type=str)
parser.add_argument('-del', action='store_true', dest='delete')
args = parser.parse_args()

bucket = args.bucket_name
file = args.file_name

if not args.delete:
    print(f"Use -del flag to delete the file.")
else:
#აქ ფაილს ვამოწმებს თუარის ბაკეტში
    objects = s3.list_objects_v2(Bucket=bucket)
    files = [obj['Key'] for obj in objects.get('Contents', [])]

    if file not in files:
        print(f"File '{file}' does not exist in bucket '{bucket}'.")
    else:
        s3.delete_object(Bucket=bucket, Key=file)
        print(f"File '{file}' deleted successfully from bucket '{bucket}'!")