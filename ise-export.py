#!/usr/bin/env python3
"""
Usage: ./ise-export.py [environment]

The script will:
1. Connect to ISE server
2. Generate endpoint report
3. Copy report to NFS repository
4. (Optional) Upload to S3 bucket

Note: This script is designed to run as a cron job.
Environment can be specified as argument (e.g., prod, staging, dev)
"""

__author__ = "Andre Klyuchka"
__email__ = "aklyuchk@cisco.com"
__website__ = "https://codeyo.net"
__license__ = "MIT - https://mit-license.org/"

import paramiko
import os
import sys
import logging
from datetime import datetime
import time
import boto3
from botocore.exceptions import ClientError
from pathlib import Path
from dotenv import load_dotenv

# Get the directory where the script is located
SCRIPT_DIR = Path(__file__).parent.absolute()

# Configure logging with absolute paths
log_file = SCRIPT_DIR / 'ise_export.log'
logging.basicConfig(
    filename=str(log_file),
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Also log to console
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)

def load_environment(env_name=None):
    """Load environment variables from .env file
    
    Args:
        env_name (str, optional): Environment name (prod, staging, dev). 
                                 If None, uses default .env file
    """
    if env_name:
        env_file = SCRIPT_DIR / f'.env.{env_name}'
    else:
        env_file = SCRIPT_DIR / '.env'
    
    if not env_file.exists():
        logging.error(f"Environment file not found: {env_file}")
        sys.exit(1)
    
    load_dotenv(env_file)
    logging.info(f"Loaded environment from {env_file}")

def get_config():
    """Get configuration from environment variables with validation"""
    required_vars = [
        'ISE_HOST',
        'ISE_USER',
        'ISE_KEY',
        'NFS_PATH',
        'AWS_REGION',
        'S3_BUCKET'
    ]
    
    config = {}
    missing_vars = []
    
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing_vars.append(var)
        config[var] = value
    
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)
    
    # Optional variables with defaults
    config['S3_PREFIX'] = os.getenv('S3_PREFIX', 'ise-reports/')
    
    return config

def check_aws_credentials():
    """Check if AWS credentials are properly configured"""
    try:
        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials is None:
            logging.error("AWS credentials not found. Please configure AWS credentials.")
            return False
        return True
    except Exception as e:
        logging.error(f"Error checking AWS credentials: {str(e)}")
        return False

def upload_to_s3(file_path, bucket, object_name=None, prefix=None):
    """Upload a file to an S3 bucket

    :param file_path: File to upload
    :param bucket: Bucket to upload to
    :param object_name: S3 object name. If not specified, file_path is used
    :param prefix: Optional prefix for the S3 object
    :return: True if file was uploaded, else False
    """
    # If S3 object_name was not specified, use file_path
    if object_name is None:
        object_name = os.path.basename(file_path)

    # Add prefix if specified
    if prefix:
        object_name = f"{prefix}{object_name}"

    # Upload the file
    try:
        s3_client = boto3.client('s3', region_name=os.getenv('AWS_REGION'))
        s3_client.upload_file(file_path, bucket, object_name)
        logging.info(f"Successfully uploaded {file_path} to s3://{bucket}/{object_name}")
        return True
    except ClientError as e:
        logging.error(f"Error uploading to S3: {str(e)}")
        return False

def wait_for_prompt(channel, prompt, timeout=30):
    """Wait for a specific prompt with timeout"""
    start_time = time.time()
    buffer = ""
    while time.time() - start_time < timeout:
        if channel.recv_ready():
            chunk = channel.recv(1024).decode('utf-8')
            buffer += chunk
            logging.info(f"Received: {chunk.strip()}")
            if prompt in buffer:
                return True
        time.sleep(0.1)
    return False

def main():
    # Get environment name from command line argument
    env_name = sys.argv[1] if len(sys.argv) > 1 else None
    load_environment(env_name)
    
    # Get configuration
    config = get_config()
    
    logging.info(f"Starting ISE export process for environment: {env_name or 'default'}")
    
    # Check AWS credentials before proceeding
    if not check_aws_credentials():
        sys.exit(1)
    
    try:
        # Create SSH client
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        logging.info(f"Connecting to {config['ISE_HOST']}...")
        ssh.connect(
            config['ISE_HOST'],
            username=config['ISE_USER'],
            key_filename=os.path.expanduser(config['ISE_KEY'])
        )
        logging.info("Connected successfully")
        
        # Create interactive shell
        channel = ssh.invoke_shell()
        logging.info("Interactive shell created")
        
        # Wait for initial prompt
        if not wait_for_prompt(channel, "ise-ppan-cx/admin#"):
            logging.error("Timeout waiting for initial prompt")
            sys.exit(1)
        
        # 1. Generate the export file
        logging.info("Generating export file")
        
        # Enter ISE configuration
        channel.send("application configure ise\n")
        if not wait_for_prompt(channel, "Selection configuration option"):
            logging.error("Timeout waiting for menu")
            sys.exit(1)
        
        # Select option 16
        channel.send("16\n")
        if not wait_for_prompt(channel, "Starting to generate All Endpoints report"):
            logging.error("Timeout waiting for report start")
            sys.exit(1)
        
        # Wait for report completion
        if not wait_for_prompt(channel, "Completed generating All Endpoints report"):
            logging.error("Timeout waiting for report completion")
            sys.exit(1)
        
        # Exit menu
        channel.send("0\n")
        if not wait_for_prompt(channel, "ise-ppan-cx/admin#"):
            logging.error("Timeout waiting for admin prompt")
            sys.exit(1)
        
        # 2. Copy the file from ISE to NFS repository
        logging.info("Copying file from ISE to NFS repository")
        channel.send(f"copy disk:/{CSV_FILE} repository NFS\n")
        if not wait_for_prompt(channel, "ise-ppan-cx/admin#"):
            logging.error("Timeout waiting for copy completion")
            sys.exit(1)
        
        # Close SSH connection
        channel.close()
        ssh.close()
        logging.info("SSH connection closed")
        
        # 3. Upload the file to S3 using boto3
        logging.info("Uploading file to S3 bucket")
        if os.path.exists(f"{config['NFS_PATH']}/{CSV_FILE}"):
            if not upload_to_s3(
                f"{config['NFS_PATH']}/{CSV_FILE}",
                config['S3_BUCKET'],
                prefix=config['S3_PREFIX']
            ):
                logging.error("Failed to upload file to S3")
                sys.exit(1)
        else:
            logging.error(f"File not found: {config['NFS_PATH']}/{CSV_FILE}")
            sys.exit(1)
        
        logging.info("ISE export process completed successfully")
        
    except paramiko.SSHException as e:
        logging.error(f"SSH error: {str(e)}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main() 