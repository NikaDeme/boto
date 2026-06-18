import os, sys, time, socket, ipaddress, argparse, urllib.request
import boto3
from botocore.exceptions import ClientError

parser = argparse.ArgumentParser()
parser.add_argument("--vpc-id",       required=True)
parser.add_argument("--subnet-id",    required=True)
parser.add_argument("--rds-password", required=True)
parser.add_argument("--region",       default="us-east-1")
parser.add_argument("--key-name",     default="my-key")
args = parser.parse_args()

ec2 = boto3.client("ec2", region_name=args.region)
rds = boto3.client("rds", region_name=args.region)

# --- My IP ---
with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5) as r:
    my_ip = r.read().decode().strip()
print(f"Your IP: {my_ip}")


images = ec2.describe_images(
    Owners=["amazon"],
    Filters=[{"Name": "name", "Values": ["al2023-ami-*-x86_64"]},
             {"Name": "state", "Values": ["available"]}]
)["Images"]
ami_id = sorted(images, key=lambda i: i["CreationDate"], reverse=True)[0]["ImageId"]
print(f"AMI: {ami_id}")

# --- Key pair ---
pem = f"{args.key_name}.pem"
key_material = ec2.create_key_pair(KeyName=args.key_name)["KeyMaterial"]
open(pem, "w").write(key_material)
os.chmod(pem, 0o400)
print(f"Key: {pem}")

# --- Security group (SSH + MySQL open) ---
sg_id = ec2.create_security_group(
    GroupName="launch-sg", Description="SSH + MySQL", VpcId=args.vpc_id
)["GroupId"]
ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[
    {"IpProtocol": "tcp", "FromPort": 22,   "ToPort": 22,   "IpRanges": [{"CidrIp": f"{my_ip}/32"}]},
    {"IpProtocol": "tcp", "FromPort": 3306, "ToPort": 3306, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
])
print(f"Security group: {sg_id}")

#EC2 instance 
instance_id = ec2.run_instances(
    ImageId=ami_id, InstanceType="t3.micro", MinCount=1, MaxCount=1,
    NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": args.subnet_id,
                        "Groups": [sg_id], "AssociatePublicIpAddress": True}],
    KeyName=args.key_name,
)["Instances"][0]["InstanceId"]
print(f"EC2: {instance_id} — waiting...")
ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
public_ip = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0].get("PublicIpAddress")
print(f"EC2 public IP: {public_ip}")

# --- RDS subnet group (all subnets in VPC) ---
all_subnets = [s["SubnetId"] for s in ec2.describe_subnets(
    Filters=[{"Name": "vpc-id", "Values": [args.vpc_id]}])["Subnets"]]
rds.create_db_subnet_group(
    DBSubnetGroupName="launch-rds-subnets",
    DBSubnetGroupDescription="RDS subnets",
    SubnetIds=all_subnets,
)

#RDS MySQL 
rds.create_db_instance(
    DBInstanceIdentifier="my-mysql",
    DBName="appdb",
    DBInstanceClass="db.r5.2xlarge",  
    Engine="mysql",
    EngineVersion="8.0",
    MasterUsername="admin",
    MasterUserPassword=args.rds_password,
    AllocatedStorage=100,
    StorageType="gp3",
    VpcSecurityGroupIds=[sg_id],
    DBSubnetGroupName="launch-rds-subnets",
    PubliclyAccessible=True,
    BackupRetentionPeriod=7,
)
print("RDS creating — waiting (5-15 min)...")

deadline = time.time() + 1800
while time.time() < deadline:
    info = rds.describe_db_instances(DBInstanceIdentifier="my-mysql")["DBInstances"][0]
    if info["DBInstanceStatus"] == "available":
        rds_host = info["Endpoint"]["Address"]
        rds_port = info["Endpoint"]["Port"]
        break
    print(".", end="", flush=True)
    time.sleep(30)

print(f"\n\n{'='*50}")
print("DONE")
print(f"{'='*50}")
print(f"SSH:      ssh -i {pem} ec2-user@{public_ip}")
print(f"DB host:  {rds_host}")
print(f"DB port:  {rds_port}")
print(f"DB user:  admin")
print(f"DB name:  appdb")
print(f"{'='*50}")