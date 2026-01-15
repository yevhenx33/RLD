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

def create_wiki_database(parent_id):
    url = "https://api.notion.com/v1/databases"
    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": "Engineering Wiki"}}],
        "icon": {"emoji": "📚"},
        "properties": {
            "Doc Name": {"title": {}},
            "Tags": {
                "multi_select": {
                    "options": [
                        {"name": "Frontend", "color": "blue"},
                        {"name": "Backend", "color": "green"},
                        {"name": "Smart Contracts", "color": "purple"},
                        {"name": "DevOps", "color": "gray"},
                        {"name": "Research", "color": "orange"}
                    ]
                }
            },
            "Type": {
                "select": {
                    "options": [
                        {"name": "Spec", "color": "yellow"},
                        {"name": "Guide", "color": "blue"},
                        {"name": "Note", "color": "gray"},
                        {"name": "Snippet", "color": "pink"}
                    ]
                }
            },
            "Last Edited": {"last_edited_time": {}}
        }
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code == 200:
        return response.json()["url"]
    else:
        print(f"Error creating Wiki DB: {response.text}")
        return None

def create_content_calendar(parent_id):
    url = "https://api.notion.com/v1/databases"
    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": "Content & Launch Calendar"}}],
        "icon": {"emoji": "🗓️"},
        "properties": {
            "Content Title": {"title": {}},
            "Publish Date": {"date": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "Idea", "color": "gray"},
                        {"name": "Drafting", "color": "yellow"},
                        {"name": "In Review", "color": "blue"},
                        {"name": "Scheduled", "color": "purple"},
                        {"name": "Published", "color": "green"}
                    ]
                }
            },
            "Channel": {
                "select": {
                    "options": [
                        {"name": "Twitter / X", "color": "blue"},
                        {"name": "Blog", "color": "orange"},
                        {"name": "LinkedIn", "color": "blue"},
                        {"name": "Email / Newsletter", "color": "yellow"},
                        {"name": "Telegram", "color": "blue"}
                    ]
                }
            },
            "Link": {"url": {}}
        }
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code == 200:
        return response.json()["url"]
    else:
        print(f"Error creating Calendar DB: {response.text}")
        return None

def main():
    print("Setting up Wiki and Content Calendar...")
    
    # 1. Wiki
    print("Creating 'Engineering Wiki'...")
    wiki_url = create_wiki_database(RLD_PROJECT_PAGE_ID)
    if wiki_url:
        print(f"Wiki Created: {wiki_url}")
    
    # 2. Calendar
    print("Creating 'Content & Launch Calendar'...")
    cal_url = create_content_calendar(RLD_PROJECT_PAGE_ID)
    if cal_url:
        print(f"Calendar Created: {cal_url}")

if __name__ == "__main__":
    main()
