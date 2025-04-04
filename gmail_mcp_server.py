from typing import Any
import argparse
import os
import asyncio
import logging
import base64
from email.message import EmailMessage
from email.header import decode_header
from base64 import urlsafe_b64decode
from email import message_from_bytes
import webbrowser
import sys
import json

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.prompts import base
from mcp.types import TextContent
from mcp import types
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
import mcp.server.stdio

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Define the scopes for Gmail API access
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server instance
mcp = FastMCP("Gmail")

class GmailService:
    def __init__(self, service):
        self.service = service
        self.user_email = self._get_user_email()

    def _get_user_email(self) -> str:
        """Get user email address"""
        profile = self.service.users().getProfile(userId='me').execute()
        return profile.get('emailAddress', '')

@mcp.tool()
async def send_email(recipient_id: str, subject: str, message: str) -> dict:
    """Creates and sends an email message"""
    try:
        message_obj = EmailMessage()
        message_obj.set_content(message)
        
        message_obj['To'] = recipient_id
        message_obj['From'] = gmail_service.user_email
        message_obj['Subject'] = subject

        encoded_message = base64.urlsafe_b64encode(message_obj.as_bytes()).decode()
        create_message = {'raw': encoded_message}
        
        send_message = await asyncio.to_thread(
            lambda: gmail_service.service.users().messages().send(userId="me", body=create_message).execute()
        )
        logger.info(f"Message sent: {send_message['id']}")
        return {"status": "success", "message_id": send_message["id"]}
    except HttpError as error:
        return {"status": "error", "error_message": str(error)}

@mcp.tool()
async def get_unread_emails() -> list[dict[str, str]] | str:
    """Retrieves unread messages from mailbox"""
    try:
        user_id = 'me'
        query = 'in:inbox is:unread category:primary'

        response = await asyncio.to_thread(
            lambda: gmail_service.service.users().messages().list(userId=user_id, q=query).execute()
        )
        messages = []
        if 'messages' in response:
            messages.extend(response['messages'])

        while 'nextPageToken' in response:
            page_token = response['nextPageToken']
            response = await asyncio.to_thread(
                lambda: gmail_service.service.users().messages().list(
                    userId=user_id, q=query, pageToken=page_token
                ).execute()
            )
            messages.extend(response['messages'])
        return messages
    except HttpError as error:
        return f"An HttpError occurred: {str(error)}"

@mcp.tool()
async def read_email(email_id: str) -> dict[str, str] | str:
    """Retrieves email contents including to, from, subject, and contents"""
    try:
        msg = await asyncio.to_thread(
            lambda: gmail_service.service.users().messages().get(
                userId="me", id=email_id, format='raw'
            ).execute()
        )
        email_metadata = {}

        # Decode the base64URL encoded raw content
        raw_data = msg['raw']
        decoded_data = urlsafe_b64decode(raw_data)

        # Parse the RFC 2822 email
        mime_message = message_from_bytes(decoded_data)

        # Extract the email body
        body = None
        if mime_message.is_multipart():
            for part in mime_message.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode()
                    break
        else:
            body = mime_message.get_payload(decode=True).decode()
        email_metadata['content'] = body
        
        # Extract metadata
        email_metadata['subject'] = decode_mime_header(mime_message.get('subject', ''))
        email_metadata['from'] = mime_message.get('from','')
        email_metadata['to'] = mime_message.get('to','')
        email_metadata['date'] = mime_message.get('date','')
        
        logger.info(f"Email read: {email_id}")
        
        # Mark email as read
        await mark_email_as_read(email_id)

        return email_metadata
    except HttpError as error:
        return f"An HttpError occurred: {str(error)}"

@mcp.tool()
async def trash_email(email_id: str) -> str:
    """Moves email to trash given ID"""
    try:
        await asyncio.to_thread(
            lambda: gmail_service.service.users().messages().trash(
                userId="me", id=email_id
            ).execute()
        )
        logger.info(f"Email moved to trash: {email_id}")
        return "Email moved to trash successfully."
    except HttpError as error:
        return f"An HttpError occurred: {str(error)}"

@mcp.tool()
async def mark_email_as_read(email_id: str) -> str:
    """Marks email as read given ID"""
    try:
        await asyncio.to_thread(
            lambda: gmail_service.service.users().messages().modify(
                userId="me", id=email_id, body={'removeLabelIds': ['UNREAD']}
            ).execute()
        )
        logger.info(f"Email marked as read: {email_id}")
        return "Email marked as read."
    except HttpError as error:
        return f"An HttpError occurred: {str(error)}"

@mcp.tool()
async def open_email(email_id: str) -> str:
    """Opens email in browser given ID"""
    try:
        url = f"https://mail.google.com/#all/{email_id}"
        webbrowser.open(url, new=0, autoraise=True)
        return "Email opened in browser successfully."
    except HttpError as error:
        return f"An HttpError occurred: {str(error)}"

def decode_mime_header(header: str) -> str:
    """Helper function to decode encoded email headers"""
    decoded_parts = decode_header(header)
    decoded_string = ''
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            decoded_string += part.decode(encoding or 'utf-8')
        else:
            decoded_string += part
    return decoded_string

async def main():
    parser = argparse.ArgumentParser(description='Gmail Server Test')
    parser.add_argument('--creds-file-path', required=True, help='Path to credentials.json')
    parser.add_argument('--token-path', required=True, help='Path to token.json')
    args = parser.parse_args()

    # Initialize Gmail service
    print("Starting Gmail service initialization...")
    try:
        # Load credentials from file
        creds = None
        if os.path.exists(args.token_path):
            try:
                print(f"Loading credentials from {args.token_path}")
                creds = Credentials.from_authorized_user_file(args.token_path, SCOPES)
                print("Credentials loaded successfully")
            except json.JSONDecodeError:
                print("Token file exists but is invalid. Will create a new one.")
                creds = None
        
        # If there are no (valid) credentials available, let the user log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                print("Refreshing expired credentials...")
                creds.refresh(Request())
                print("Credentials refreshed successfully")
            else:
                print("No valid credentials found. Starting OAuth flow...")
                print("A browser window should open for authentication...")
                flow = InstalledAppFlow.from_client_secrets_file(args.creds_file_path, SCOPES)
                creds = flow.run_local_server(port=0)
                print("OAuth flow completed successfully")
            
            # Save the credentials for the next run
            print(f"Saving credentials to {args.token_path}")
            with open(args.token_path, 'w') as token:
                token.write(creds.to_json())
            print("Credentials saved successfully")

        # Build the Gmail service
        print("Building Gmail service...")
        service = build('gmail', 'v1', credentials=creds)
        print("Gmail service built successfully")
        
        # Initialize global Gmail service
        global gmail_service
        gmail_service = GmailService(service)
        
        # Run the MCP server
        print("Starting MCP server...")
        if len(sys.argv) > 1 and sys.argv[1] == "dev":
            await mcp.run_async()  # Run without transport for dev server
        else:
            await mcp.run_stdio_async()  # Run with stdio for direct execution
            
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())