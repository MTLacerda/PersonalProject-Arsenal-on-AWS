from aws_cdk import (
    Duration,
    aws_ec2 as ec2,
    Stack,
    RemovalPolicy,
    Aws,
    aws_events as events,
    aws_events_targets as targets,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_s3_assets as s3a,
    aws_lambda as _lambda,
    aws_iam as iam,
    CfnOutput
)
from constructs import Construct
from pathlib import Path

import socket
from datetime import datetime

#Get public IP
hostname = socket.gethostname()
ip_address = socket.gethostbyname(hostname)

#get today's date
today_date = datetime.now().strftime('%d%m%Y')



class PersonalProjectStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        
    #2. Create the EC2 instance with the required read and write permissions
    
        #2.1 Create VPC
        vpc = ec2.Vpc(self, "MyVpc-4-pipeline", max_azs=2)  # Max Availability Zones
        
        # Create security group and ingress rule to only allow ssh access from my IP
        security_group = ec2.SecurityGroup(
            scope=self,
            id="ProjectSecurityGroup",
            vpc=vpc,
            description="Allow ssh access to ec2 instances",
            allow_all_outbound=True,
        )

        security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(cidr_ip=f"147.161.190.93/32"),
            connection=ec2.Port.tcp(22),
            description="Allow SSH access only",
        )
        
        security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("0.0.0.0/0"),  # Allow access from anywhere (or replace with a specific IP range)
            connection=ec2.Port.tcp(7077),
            description="Allow access to Spark Web UI"
        )

        security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("0.0.0.0/0"),  # Allow access from anywhere (or replace with a specific IP range)
            connection=ec2.Port.tcp(8085),
            description="Allow access to Jupyter Notebook"
        )

        security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("0.0.0.0/0"),  # Allow access from anywhere (or replace with a specific IP range)
            connection=ec2.Port.tcp(5432),
            description="Allow access to PostgreSQL"
        )
        
        security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("0.0.0.0/0"),  # Allow access from anywhere (or replace with a specific IP range)
            connection=ec2.Port.tcp(8090),
            description="Allow access to Airflow Server UI"
        )
        
        

        cfn_key_pair = ec2.KeyPair(
            scope=self,
            id="ProjectKeyPair",
            key_pair_name="ProjectKey",
        )
             
        #2.2 Create the IAM role and atribute it to the instance
        ec2_role_Read_write = iam.Role(self, "Ec2S3ReadWriteRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com")
        )     

        #2.3 Create the instance
        ec2_4_pipeline = ec2.Instance(self, 'Data Aggregator Instance',
                                    instance_name= 'Data Aggregator Instance',
                                    machine_image=ec2.MachineImage.latest_amazon_linux2(),
                                    instance_type=ec2.InstanceType.of(instance_class=ec2.InstanceClass.T3,
                                                                        instance_size=ec2.InstanceSize.MEDIUM),
                                    block_devices=[  # Custom block device settings
                                    ec2.BlockDevice(
                                        device_name="/dev/xvda",  # default root device name
                                        volume=ec2.BlockDeviceVolume.ebs(20))
                                    ],# Set the size of the root EBS volume to 20GB
                                    vpc=vpc,
                                    vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                                    user_data_causes_replacement=True,
                                    associate_public_ip_address=True,
                                    security_group=security_group,
                                    key_pair=cfn_key_pair,
                                    role=ec2_role_Read_write)
        
        CfnOutput(self, "InstanceId", value=ec2_4_pipeline.instance_id)
        
        #2.4 Allow SSH access from port 22
        ec2_4_pipeline.connections.allow_from_any_ipv4(ec2.Port.tcp(22), 'Allow SSH access from the Internet')
        
        #Allow access to the ports of spark, airflow and postgres, so they can comunicate with each other
        
        #2.5 Add the user_data generator script as an asset to the ec2
        github_asset = s3a.Asset(self, 'GithubAsset',
                                            path='./personal_project/ArsenalFC-Data-Pipeline-Project/')
            
        ec2_4_pipeline.user_data.add_s3_download_command(bucket=github_asset.bucket,
                                                        bucket_key=github_asset.s3_object_key,
                                                        local_file='/home/ec2-user/ArsenalFC-Data-Pipeline-Project/')
        
        #Create Role and give permission to run the asset inside the EC2
        
        github_asset.grant_read(ec2_4_pipeline.role)
        
        ec2_4_pipeline.user_data.add_commands(
            'sudo yum -y install docker',
            'sudo usermod -a -G docker ec2-user',
            'newgrp docker',
            'sudo yum -y install python3-pip',
            'sudo pip3 install docker-compose',
            'sudo systemctl enable docker.service',
            'sudo systemctl start docker.service',
            'sudo yum update openssl',
            'pip install urllib3==1.25.11'                   
)
        
        '''
        Downgrade urllib3
        Another approach is to downgrade urllib3 to a version that's compatible with OpenSSL 1.0.2k. Specifically, urllib3 v1.26.x works with OpenSSL 1.0.2.
              '''
        ec2_4_pipeline.user_data.add_commands(
               'sudo pip3 install urllib3==1.26.6'
               )
        
        
        ec2_4_pipeline.user_data.add_commands(
            'sudo chmod -R 644 /home/ec2-user/ArsenalFC-Data-Pipeline-Project/*.zip', #Ensure the .zip file is writable and readable
            'sudo chmod -R 755 /home/ec2-user/ArsenalFC-Data-Pipeline-Project', #Ensure the directory is writable
          )
        
        ec2_4_pipeline.user_data.add_commands(
            'sudo unzip /home/ec2-user/ArsenalFC-Data-Pipeline-Project/*.zip -d /home/ec2-user/ArsenalFC-Data-Pipeline-Project'
        )
        
        '''
        Grow partition size- for, say, 20 gb
        sudo growpart /dev/nvme0n1 1
        sudo xfs_growfs /dev/nvme0n1p1
        '''

                #Create all the folder needed for the services in the docker compose, as well as the read/write permissions for that forlder
        ec2_4_pipeline.user_data.add_commands(

            'export AIRFLOW_UID=$(id -u ec2-user)',  # Get the UID for ec2-user
            'export AIRFLOW_GID=$(id -g ec2-user)'  # Get the GID for ec2-user

            'sudo chmod 777 /home/ec2-user/*' #All the permissions for the user, the group and others

        )

        ec2_4_pipeline.user_data.add_commands(
            'docker-compose -f /home/ec2-user/ArsenalFC-Data-Pipeline-Project/Docker_files/docker-compose.yml up -d'
   
)
