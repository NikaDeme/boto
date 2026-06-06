import argparse
import ipaddress
import os
import socket
import sys
import time

import boto3
from botocore.exceptions import ClientError


# ──────────────────────────────────────────────
# Argument helpers
# ──────────────────────────────────────────────

def validate_vpc_id(value):
    if not value.startswith("vpc-"):
        raise argparse.ArgumentTypeError(f"Invalid VPC ID format: '{value}'. Must start with 'vpc-'")
    return value


def validate_subnet_id(value):
    if not value.startswith("subnet-"):
        raise argparse.ArgumentTypeError(f"Invalid Subnet ID format: '{value}'. Must start with 'subnet-'")
    return value


def validate_ip(value):
    try:
        ipaddress.IPv4Address(value)
        return value
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid IP address: '{value}'")


# ──────────────────────────────────────────────
# AWS validation helpers
# ──────────────────────────────────────────────

def check_vpc_exists(ec2, vpc_id):
    """Confirm the VPC exists in AWS and return its details."""
    try:
        resp = ec2.describe_vpcs(VpcIds=[vpc_id])
        vpcs = resp["Vpcs"]
        if not vpcs:
            print(f"[ERROR] VPC '{vpc_id}' not found.")
            sys.exit(1)
        print(f"  [OK] VPC found: {vpc_id}")
        return vpcs[0]
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg  = e.response["Error"]["Message"]
        if code == "InvalidVpcID.NotFound":
            print(f"[ERROR] VPC '{vpc_id}' does not exist.")
        elif code == "UnauthorizedOperation":
            print(f"[ERROR] Permission denied when describing VPCs: {msg}")
        else:
            print(f"[ERROR] AWS error ({code}): {msg}")
        sys.exit(1)


def check_subnet_exists(ec2, subnet_id, vpc_id):
    """Confirm the subnet exists and belongs to the given VPC."""
    try:
        resp = ec2.describe_subnets(SubnetIds=[subnet_id])
        subnets = resp["Subnets"]
        if not subnets:
            print(f"[ERROR] Subnet '{subnet_id}' not found.")
            sys.exit(1)
        subnet = subnets[0]
        if subnet["VpcId"] != vpc_id:
            print(f"[ERROR] Subnet '{subnet_id}' belongs to VPC '{subnet['VpcId']}', not '{vpc_id}'.")
            sys.exit(1)
        print(f"  [OK] Subnet found: {subnet_id} (VPC: {vpc_id})")
        return subnet
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg  = e.response["Error"]["Message"]
        if code == "InvalidSubnetID.NotFound":
            print(f"[ERROR] Subnet '{subnet_id}' does not exist.")
        elif code == "UnauthorizedOperation":
            print(f"[ERROR] Permission denied when describing subnets: {msg}")
        else:
            print(f"[ERROR] AWS error ({code}): {msg}")
        sys.exit(1)


# ──────────────────────────────────────────────
# AMI lookup
# ──────────────────────────────────────────────

def get_latest_amazon_linux_3_ami(ec2):

    try:
        resp = ec2.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name",                "Values": ["al2023-ami-*-x86_64"]},
                {"Name": "state",               "Values": ["available"]},
                {"Name": "architecture",        "Values": ["x86_64"]},
                {"Name": "virtualization-type", "Values": ["hvm"]},
                {"Name": "root-device-type",    "Values": ["ebs"]},
            ],
        )
        images = resp["Images"]
        if not images:
            print("[ERROR] No Amazon Linux 3 (AL2023) AMI found in this region.")
            sys.exit(1)

        latest = sorted(images, key=lambda i: i["CreationDate"], reverse=True)[0]
        print(f"  [OK] Latest Amazon Linux 3 AMI: {latest['ImageId']} ({latest['Name']})")
        return latest["ImageId"]
    except ClientError as e:
        code = e.response["Error"]["Code"]
        print(f"[ERROR] Failed to look up AMI ({code}): {e.response['Error']['Message']}")
        sys.exit(1)


# ──────────────────────────────────────────────
# External IP detection
# ──────────────────────────────────────────────

def get_my_external_ip():

    import urllib.request
    try:
        with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5) as r:
            ip = r.read().decode().strip()
            ipaddress.IPv4Address(ip)   # sanity-check
            return ip
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        print("[ERROR] Could not determine your external IP. Use --custom-ssh-ip to provide it manually.")
        sys.exit(1)


# ──────────────────────────────────────────────
# Key pair
# ──────────────────────────────────────────────

def create_key_pair(ec2, key_name):
    """Create an EC2 key pair and save the private key as <key_name>.pem with 0400 permissions."""
    pem_file = f"{key_name}.pem"
    try:
        resp = ec2.create_key_pair(KeyName=key_name)
        private_key = resp["KeyMaterial"]

        with open(pem_file, "w") as f:
            f.write(private_key)

        # 0400 = owner read-only (required by SSH)
        os.chmod(pem_file, 0o400)
        print(f"  [OK] Key pair created: '{key_name}' — saved to {pem_file} (permissions: 0400)")
        return key_name, pem_file

    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg  = e.response["Error"]["Message"]
        if code == "InvalidKeyPair.Duplicate":
            print(f"[ERROR] Key pair '{key_name}' already exists in AWS. Choose a different name or delete it first.")
        elif code == "UnauthorizedOperation":
            print(f"[ERROR] Permission denied when creating key pair: {msg}")
        else:
            print(f"[ERROR] AWS error ({code}): {msg}")
        sys.exit(1)


# ──────────────────────────────────────────────
# Security group
# ──────────────────────────────────────────────

def create_security_group(ec2, vpc_id, sg_name, ssh_ip):
    try:
        resp = ec2.create_security_group(
            GroupName=sg_name,
            Description="Allows SSH access",
            VpcId=vpc_id,
        )
        sg_id = resp["GroupId"]
        print(f"  [OK] Security group created: {sg_id}")

        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": f"{ssh_ip}/32", "Description": "SSH access"}],
                }
            ],
        )
        print(f"  [OK] SSH (port 22) allowed from {ssh_ip}/32")
        return sg_id

    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg  = e.response["Error"]["Message"]
        if code == "InvalidGroup.Duplicate":
            print(f"[ERROR] Security group '{sg_name}' already exists in this VPC.")
        elif code == "UnauthorizedOperation":
            print(f"[ERROR] Permission denied when creating security group: {msg}")
        else:
            print(f"[ERROR] AWS error ({code}): {msg}")
        sys.exit(1)


# ──────────────────────────────────────────────
# EC2 instance
# ──────────────────────────────────────────────

def launch_instance(ec2, ami_id, instance_type, subnet_id, sg_id, key_name):
    try:
        resp = ec2.run_instances(
            ImageId=ami_id,
            InstanceType=instance_type,
            MinCount=1,
            MaxCount=1,
            SubnetId=subnet_id,
            SecurityGroupIds=[sg_id],
            KeyName=key_name,
            # Assign a public IP so we can SSH in from outside
            NetworkInterfaces=[
                {
                    "DeviceIndex": 0,
                    "SubnetId": subnet_id,
                    "Groups": [sg_id],
                    "AssociatePublicIpAddress": True,
                }
            ],
        )
        instance_id = resp["Instances"][0]["InstanceId"]
        print(f"  [OK] Instance launched: {instance_id}")
        return instance_id

    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg  = e.response["Error"]["Message"]
        if code == "UnauthorizedOperation":
            print(f"[ERROR] Permission denied when launching instance: {msg}")
        elif code == "InvalidSubnetID.NotFound":
            print(f"[ERROR] Subnet not found during instance launch: {msg}")
        else:
            print(f"[ERROR] AWS error ({code}): {msg}")
        sys.exit(1)


# ──────────────────────────────────────────────
# Wait & SSH probe
# ──────────────────────────────────────────────

def wait_for_instance(ec2, instance_id):
    print(f"  Waiting for instance {instance_id} to reach 'running' state ", end="", flush=True)
    try:
        waiter = ec2.get_waiter("instance_running")
        waiter.wait(InstanceIds=[instance_id])
    except ClientError as e:
        print()
        print(f"[ERROR] Error while waiting for instance: {e.response['Error']['Message']}")
        sys.exit(1)

    # Refresh instance details to get the public IP
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    instance = resp["Reservations"][0]["Instances"][0]
    public_ip = instance.get("PublicIpAddress")
    print(f"\n  [OK] Instance is running. Public IP: {public_ip}")
    return public_ip


def wait_for_ssh(host, port=22, timeout=120, interval=5):
    print(f"  Probing SSH on {host}:{port} (timeout: {timeout}s) ", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=interval):
                print(f"\n  [OK] SSH port 22 is open on {host}")
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            print(".", end="", flush=True)
            time.sleep(interval)

    print(f"\n[WARNING] SSH port 22 did not become available within {timeout} seconds.")
    print("  The instance may still be initialising. Try connecting manually later.")
    return False


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Launch an EC2 instance with SSH access into an existing VPC/Subnet.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Required network arguments
    parser.add_argument("--vpc-id",    required=True, type=validate_vpc_id,    help="Target VPC ID (e.g. vpc-0abc123)")
    parser.add_argument("--subnet-id", required=True, type=validate_subnet_id, help="Target Subnet ID (e.g. subnet-0abc123)")

    # Optional / configurable
    parser.add_argument("--region",          default="us-east-1",  help="AWS region")
    parser.add_argument("--instance-type",   default="t3.micro",   help="EC2 instance type")
    parser.add_argument("--key-name",        default="my-ec2-key", help="Name for the new EC2 key pair")
    parser.add_argument("--sg-name",         default="ec2-ssh-sg", help="Name for the new security group")
    parser.add_argument(
        "--custom-ssh-ip",
        type=validate_ip,
        default=None,
        help="IPv4 address to whitelist for SSH. Auto-detected if omitted.",
    )

    args = parser.parse_args()

    ec2 = boto3.client("ec2", region_name=args.region)

    # ──Validate VPC and Subnet ──────────────────────────────────────
    print("\n[1/6] Validating VPC and Subnet in AWS...")
    check_vpc_exists(ec2, args.vpc_id)
    check_subnet_exists(ec2, args.subnet_id, args.vpc_id)

    # ──Find latest Amazon Linux 3 AMI ───────────────────────────────
    print("\n[2/6] Looking up latest Amazon Linux 3 AMI...")
    ami_id = get_latest_amazon_linux_3_ami(ec2)

    # ──Determine SSH IP ─────────────────────────────────────────────
    print("\n[3/6] Determining SSH source IP...")
    if args.custom_ssh_ip:
        ssh_ip = args.custom_ssh_ip
        print(f"  [OK] Using custom SSH IP: {ssh_ip}")
    else:
        ssh_ip = get_my_external_ip()
        print(f"  [OK] Auto-detected external IP: {ssh_ip}")

    # ──Create Key Pair ───────────────────────────────────────────────
    print("\n[4/6] Creating key pair...")
    key_name, pem_file = create_key_pair(ec2, args.key_name)

    # ──Create Security Group & Launch Instance ───────────────────────
    print("\n[5/6] Creating security group...")
    sg_id = create_security_group(ec2, args.vpc_id, args.sg_name, ssh_ip)

    print("\n[5b] Launching EC2 instance...")
    instance_id = launch_instance(ec2, ami_id, args.instance_type, args.subnet_id, sg_id, key_name)

    # ──Wait for running + SSH probe ──────────────────────────────────
    print("\n[6/6] Waiting for instance to be ready...")
    public_ip = wait_for_instance(ec2, instance_id)

    if public_ip:
        wait_for_ssh(public_ip)
    else:
        print("[WARNING] No public IP assigned. Cannot probe SSH. Check your subnet's auto-assign public IP setting.")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 55)
    print("  LAUNCH COMPLETE")
    print("═" * 55)
    print(f"  Instance ID  : {instance_id}")
    print(f"  Public IP    : {public_ip}")
    print(f"  AMI          : {ami_id}")
    print(f"  Key file     : {pem_file}  (permissions: 0400)")
    print(f"  SSH command  : ssh -i {pem_file} ec2-user@{public_ip}")
    print("═" * 55 + "\n")


if __name__ == "__main__":
    main()