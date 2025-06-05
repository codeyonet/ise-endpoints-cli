#!/usr/bin/env python3
"""
Usage: ./ise-export-test.py

This is a test script to verify the ISE export process. 
It uses a pre-signed URL to upload the file to S3, and really not suitable to work as a cron job.
For that use ise-export.py instead.

The script will:
1. Connect to ISE server
2. Generate endpoint report
3. Copy report to NFS repository
4. (Optional) Upload to S3 bucket using pre-signed URL
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

# Configure logging
logging.basicConfig(
    filename='ise_export.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Also log to console
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)

# Variables
ISE_HOST = "1.2.3.4"
ISE_USER = "iseadmin"
ISE_KEY = os.path.expanduser("~/.ssh/ise")
NFS_PATH = "/home/nfsshare"
PRESIGNED_URL = f"https://your-s3-bucket.s3.amazonaws.com/FullReport_{datetime.now().strftime('%d-%b-%Y')}.csv?AWSAccessKeyId=..."  # Replace with your pre-signed URL
TODAY = datetime.now().strftime("%d-%b-%Y")
CSV_FILE = f"FullReport_{TODAY}.csv"

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
    logging.info("Starting ISE export process")
    
    try:
        # Create SSH client
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        logging.info(f"Connecting to {ISE_HOST}...")
        ssh.connect(ISE_HOST, username=ISE_USER, key_filename=ISE_KEY)
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
        
        # 3. Upload the file to S3 using curl
        logging.info("Uploading file to S3 bucket")
        if os.path.exists(f"{NFS_PATH}/{CSV_FILE}"):
            upload_cmd = f'curl -X PUT -T "{NFS_PATH}/{CSV_FILE}" "{PRESIGNED_URL}"'
            if os.system(upload_cmd) != 0:
                logging.error("Failed to upload file to S3")
                sys.exit(1)
        else:
            logging.error(f"File not found: {NFS_PATH}/{CSV_FILE}")
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