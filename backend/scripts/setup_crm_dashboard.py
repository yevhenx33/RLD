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

# Known IDs from previous steps
RLD_PROJECT_PAGE_ID = "2e8c2f1b-c76a-80b3-b758-e590a1544c4d"
NETWORK_CRM_DB_ID = "2e8c2f1b-c76a-81d4-b34a-f1f5133eb92a"

def create_tasks_database(parent_id, related_db_id):
    url = "https://api.notion.com/v1/databases"
    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": "CRM Tasks"}}],
        "properties": {
            "Task Name": {"title": {}},
            "Due Date": {"date": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "To Do", "color": "gray"},
                        {"name": "In Progress", "color": "blue"},
                        {"name": "Done", "color": "green"}
                    ]
                }
            },
            "Priority": {
                "select": {
                    "options": [
                        {"name": "Normal", "color": "gray"},
                        {"name": "High", "color": "orange"},
                        {"name": "Urgent", "color": "red"}
                    ]
                }
            },
            "Type": {
                "select": {
                    "options": [
                        {"name": "Follow-up", "color": "purple"},
                        {"name": "Meeting", "color": "yellow"},
                        {"name": "Email", "color": "blue"},
                        {"name": "Call", "color": "green"}
                    ]
                }
            },
            "Contact": {
                "relation": {
                    "database_id": related_db_id,
                    "type": "dual_property", 
                    "dual_property": {} 
                }
            }
        }
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code == 200:
        return response.json()["id"], response.json()["url"]
    else:
        print(f"Error creating Tasks DB: {response.text}")
        return None, None

def create_dashboard_page(parent_id, tasks_db_url, contacts_db_url):
    url = "https://api.notion.com/v1/pages"
    
    children = [
        {
            "object": "block",
            "type": "heading_1",
            "heading_1": {"rich_text": [{"type": "text", "text": {"content": "CRM Control Panel"}}]}
        },
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": "Manage your relationships and follow-ups here."}}],
                "icon": {"emoji": "🎛️"}
            }
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🔔 Notifications (Tasks)"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "View your "}},
                    {"type": "text", "text": {"content": "CRM Tasks Database", "link": {"url": tasks_db_url}}},
                    {"type": "text", "text": {"content": ". (Tip: Create a Linked View here filtered by 'Due Date <= Today')"}}
                ]
            }
        },
        {
             "object": "block",
             "type": "divider",
             "divider": {}
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🚀 Active Pipeline"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "View your "}},
                    {"type": "text", "text": {"content": "Network CRM Database", "link": {"url": contacts_db_url}}},
                    {"type": "text", "text": {"content": ". (Tip: Create a Linked View here with Board Layout)"}}
                ]
            }
        }
    ]

    payload = {
        "parent": {"page_id": parent_id},
        "properties": {
            "title": [{"text": {"content": "CRM Dashboard"}}]
        },
        "children": children
    }
    
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code == 200:
        return response.json()["url"]
    else:
        print(f"Error creating Dashboard Page: {response.text}")
        return None

def main():
    print("Setting up CRM Control Panel...")
    
    # 1. Create Tasks Database
    print("Creating 'CRM Tasks' Database...")
    tasks_id, tasks_url = create_tasks_database(RLD_PROJECT_PAGE_ID, NETWORK_CRM_DB_ID)
    
    if not tasks_id:
        print("Failed to create Tasks DB. Exiting.")
        return
        
    print(f"Tasks DB Created: {tasks_url}")
    
    # Contacts URL (we don't have it explicitly stored from previous script output, but we can guess or just link to parent)
    # Actually, let's just use a placeholder text or try to find it if we want to be fancy. 
    # For now, we'll just link to the DB ID basically.
    # But wait, create_tasks_database returned the URL for tasks.
    
    # 2. Create Dashboard Page
    print("Creating 'CRM Dashboard' Page...")
    # We construct a rough URL for contacts DB based on ID if needed, or query it.
    # Let's simple query it to get the URL
    contacts_url = "https://www.notion.so/" + NETWORK_CRM_DB_ID.replace("-", "")
    
    dash_url = create_dashboard_page(RLD_PROJECT_PAGE_ID, tasks_url, contacts_url)
    
    if dash_url:
        print(f"Dashboard Created: {dash_url}")
    else:
        print("Failed to create Dashboard.")

if __name__ == "__main__":
    main()
