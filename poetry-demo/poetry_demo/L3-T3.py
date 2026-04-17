import boto3
import argparse
from dotenv import load_dotenv
from datetime import timezone

load_dotenv()

s3 = boto3.client('s3')

parser = argparse.ArgumentParser()
parser.add_argument('bucket_name', type=str)
parser.add_argument('file_name', nargs='?', default=None)
parser.add_argument('-versioning', action='store_true', help='Check if versioning is enabled')
parser.add_argument('-versions', action='store_true', help='List all versions of a file')
parser.add_argument('-restore', action='store_true', help='Restore previous version of a file')

args = parser.parse_args()

bucket = args.bucket_name
file = args.file_name

# ─────────────────────────────────────────
# FLAG: -versioning
# Check if versioning is enabled on bucket
# ─────────────────────────────────────────
if args.versioning:
    response = s3.get_bucket_versioning(Bucket=bucket)
    status = response.get('Status', 'Disabled')

    if status == 'Enabled':
        print(f"Versioning is ENABLED on bucket '{bucket}'.")
    else:
        print(f"Versioning is DISABLED on bucket '{bucket}'.")
        print(f"Enable it with: aws s3api put-bucket-versioning --bucket {bucket} --versioning-configuration Status=Enabled")


elif args.versions:
    if not file:
        print("Please provide a file name.")
    else:
        response = s3.list_object_versions(Bucket=bucket, Prefix=file)
        versions = response.get('Versions', [])

        if not versions:
            print(f"No versions found for '{file}' in bucket '{bucket}'.")
        else:
            print(f"\nVersions of '{file}' in bucket '{bucket}':\n")
            for i, v in enumerate(versions):
                date = v['LastModified'].astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                latest = "current" if v['IsLatest'] else ""
                print(f"  [{i+1}] Version ID : {v['VersionId']}")
                print(f"       Created    : {date}")
                print(f"       Size       : {v['Size']} bytes {latest}")
                print()


elif args.restore:
    if not file:
        print("Please provide a file name.")
    else:
        response = s3.list_object_versions(Bucket=bucket, Prefix=file)
        versions = response.get('Versions', [])

        if len(versions) < 2:
            print(f"No previous version found for '{file}'.")
        else:
            # versions[0] is current, versions[1] is previous
            previous = versions[1]
            previous_version_id = previous['VersionId']
            date = previous['LastModified'].astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

            print(f"Found previous version:")
            print(f"  Version ID : {previous_version_id}")
            print(f"  Created    : {date}")
            print(f"  Size       : {previous['Size']} bytes")

            # Copy previous version as new version
            s3.copy_object(
                Bucket=bucket,
                CopySource={
                    'Bucket': bucket,
                    'Key': file,
                    'VersionId': previous_version_id
                },
                Key=file
            )
            print(f"\n Previous version restored as new version for '{file}'!")

else:
    print("Please provide a valid flag: -versioning | -versions | -restore")