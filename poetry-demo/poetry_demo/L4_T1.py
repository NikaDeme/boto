import boto3
import magic
import click
import os

def get_s3_folder(mime_type):
    if mime_type.startswith('image/'):
        return 'images'
    elif mime_type.startswith('video/'):
        return 'videos'
    elif mime_type in ['application/pdf', 'text/plain', 'application/msword',
                       'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
        return 'documents'
    elif mime_type in ['application/zip', 'application/x-tar', 'application/x-rar-compressed']:
        return 'archives'
    else:
        return 'others'

@click.command()
@click.argument('file_path', type=click.Path(exists=True))
@click.option('--bucket', required=True, help='S3 bucket name')
@click.option('--region', default='us-east-1', help='AWS region')



def upload(file_path, bucket, region):
    mime = magic.Magic(mime=True)
    mime_type = mime.from_file(file_path)

    folder = get_s3_folder(mime_type)

    file_name = os.path.basename(file_path)

    s3_key = f"{folder}/{file_name}"

    s3 = boto3.client('s3', region_name=region)

    try:
        s3.upload_file(file_path, bucket, s3_key)
        click.echo(f"Uploaded '{file_name}' to '{bucket}/{s3_key}' (MIME: {mime_type})")
    except Exception as e:
        click.echo(f"Error uploading file: {e}")

if __name__ == '__main__':
    upload()