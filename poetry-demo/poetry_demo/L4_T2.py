import boto3
import click
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

@click.command()
@click.option('--bucket', required=True, help='S3 bucket name')
@click.argument('keys', nargs=-1)
@click.option('--region', default='us-east-1', help='AWS region')
@click.option('--delete-current', is_flag=True, help='Also delete current versions')
def cleanup_versions(bucket, keys, region, delete_current):
    """
    Deletes S3 object versions older than 6 months for given keys.
    """

    s3 = boto3.client('s3', region_name=region)

    cutoff_date = datetime.now(timezone.utc) - relativedelta(months=6)

    for key in keys:
        click.echo(f"\nProcessing: {key}")

        paginator = s3.get_paginator('list_object_versions')
        pages = paginator.paginate(Bucket=bucket, Prefix=key)

        for page in pages:
            versions = page.get('Versions', [])

            for version in versions:
                version_id = version['VersionId']
                last_modified = version['LastModified']
                is_latest = version['IsLatest']

                if is_latest and not delete_current:
                    continue

                if last_modified < cutoff_date:
                    try:
                        s3.delete_object(
                            Bucket=bucket,
                            Key=key,
                            VersionId=version_id
                        )
                        click.echo(
                            f"Deleted version {version_id} (LastModified: {last_modified})"
                        )
                    except Exception as e:
                        click.echo(f"Error deleting {version_id}: {e}")

if __name__ == "__main__":
    cleanup_versions()