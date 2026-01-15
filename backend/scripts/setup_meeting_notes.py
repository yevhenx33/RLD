import os
import requests
import json
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.getcwd(), '.env')
load_dotenv(dotenv_path)

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_VERSION = "2022-06-28"
HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": NOTION_VERSION
}

# IDs
RLD_PROJECT_PAGE_ID = "2e8c2f1b-c76a-80b3-b758-e590a1544c4d"
NETWORK_CRM_DB_ID = "2e8c2f1b-c76a-81d4-b34a-f1f5133eb92a" # The active CRM DB

def create_meetings_database(parent_id, related_db_id):
    url = "https://api.notion.com/v1/databases"
    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": "Meetings & Interactions"}}],
        "properties": {
            "Meeting Name": {"title": {}},
            "Date": {"date": {}},
            "Attendees": {
                "relation": {
                    "database_id": related_db_id,
                    "type": "dual_property", 
                    "dual_property": {} 
                }
            },
            "Type": {
                "select": {
                    "options": [
                        {"name": "Call/Zoom", "color": "blue"},
                        {"name": "In-Person", "color": "green"},
                        {"name": "Conference", "color": "orange"},
                        {"name": "Email Thread", "color": "gray"},
                        {"name": "Telegram Chat", "color": "purple"}
                    ]
                }
            },
            "Summary": {"rich_text": {}}
        }
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code == 200:
        return response.json()["id"], response.json()["url"]
    else:
        print(f"Error creating Meetings DB: {response.text}")
        return None, None

def main():
    print("Setting up Meeting Notes System...")
    
    db_id, db_url = create_meetings_database(RLD_PROJECT_PAGE_ID, NETWORK_CRM_DB_ID)
    
    if db_id:
        print(f"Meetings & Interactions Database Created Successfully!")
        print(f"URL: {db_url}")
        print("Note: The 'Attendees' property is now linked to your 'Network CRM'.")
    else:
        print("Failed to setup database.")

if __name__ == "__main__":
    main()
