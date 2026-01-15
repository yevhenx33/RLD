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

# IDs (Derived from previous steps)
NETWORK_CRM_DB_ID = "2e8c2f1b-c76a-81d4-b34a-f1f5133eb92a"
CRM_TASKS_DB_ID = "2e8c2f1bc76a81c0bbc2dd718c116083" 
MEETINGS_DB_ID = "2e8c2f1bc76a81368b3ad30d13ea2113"
WIKI_DB_ID = "2e8c2f1bc76a8159bec4e5f0950f9748"
CALENDAR_DB_ID = "2e8c2f1bc76a8186b4f5fc52c36f8425"

def get_first_contact_id():
    """Fetches the first contact to use for linking examples."""
    url = f"https://api.notion.com/v1/databases/{NETWORK_CRM_DB_ID}/query"
    response = requests.post(url, headers=HEADERS, json={"page_size": 1})
    if response.status_code == 200:
        results = response.json().get("results", [])
        if results:
            return results[0]["id"]
    return None

def create_page(db_id, properties, children):
    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": db_id},
        "properties": properties,
        "children": children
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code != 200:
        print(f"Error creating page in DB {db_id}: {response.text}")
    else:
        print(f"Created page in DB {db_id}")

def seed_wiki():
    print("Seeding Wiki...")
    prop = {
        "Doc Name": {"title": [{"text": {"content": "📘 READ ME: How to use this Engineering Wiki"}}]},
        "Tags": {"multi_select": [{"name": "Research"}, {"name": "DevOps"}]},
        "Type": {"select": {"name": "Guide"}}
    }
    children = [
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "Welcome to your Engineering Brain 🧠"}}]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "This database is your single source of truth for technical documentation."}}]}},
        {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"text": {"content": "How to organize:"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "TAGS: Use tags (Frontend, Smart Contracts) to make finding things easy."}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "TYPES: Distinguish between 'Specs' (Plan), 'Guides' (How-to), and 'Snippets' (Code)."}}]}},
        {"object": "block", "type": "callout", "callout": {"rich_text": [{"text": {"content": "Pro Tip: Move your 'RLD Paper' breakdown here so it's not lost in chat logs!"}}], "icon": {"emoji": "💡"}}}
    ]
    create_page(WIKI_DB_ID, prop, children)

def seed_calendar():
    print("Seeding Calendar...")
    prop = {
        "Content Title": {"title": [{"text": {"content": "🚀 Example: ETHcc Launch Announcement"}}]},
        "Status": {"select": {"name": "Drafting"}},
        "Channel": {"select": {"name": "Twitter / X"}},
        "Publish Date": {"date": {"start": "2025-07-01"}} # Hypothetical
    }
    children = [
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "Content Strategy"}}]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Use this card to draft your tweet or blog post."}}]}},
        {"object": "block", "type": "to_do", "to_do": {"rich_text": [{"text": {"content": "Draft the hook"}}]}},
        {"object": "block", "type": "to_do", "to_do": {"rich_text": [{"text": {"content": "Create graphics"}}], "checked": False}},
        {"object": "block", "type": "quote", "quote": {"rich_text": [{"text": {"content": "Draft: RLD is now live at ETHcc! Trade rates with 0 slippage. ⚡"}}], "color": "gray_background"}}
    ]
    create_page(CALENDAR_DB_ID, prop, children)

def seed_meetings(contact_id):
    print("Seeding Meetings...")
    prop = {
        "Meeting Name": {"title": [{"text": {"content": "📝 Example: Intro Call"}}]},
        "Type": {"select": {"name": "Call/Zoom"}},
        "Date": {"date": {"start": "2025-01-14"}},
    }
    # Link to contact if available
    if contact_id:
        prop["Attendees"] = {"relation": [{"id": contact_id}]}

    children = [
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "Meeting Minutes"}}]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Use this space to record granular details that don't fit in the CRM 'Notes' field."}}]}},
        {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"text": {"content": "Workflow:"}}]}},
        {"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"text": {"content": "Open a Contact in 'Network CRM'."}}]}},
        {"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"text": {"content": "Click 'Related Meetings'."}}]}},
        {"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"text": {"content": "Add New -> This automatically links the meeting to the person."}}]}},
    ]
    create_page(MEETINGS_DB_ID, prop, children)

def seed_tasks(contact_id):
    print("Seeding Tasks...")
    prop = {
        "Task Name": {"title": [{"text": {"content": "✅ How to: Follow Up Task"}}]},
        "Status": {"select": {"name": "To Do"}},
        "Priority": {"select": {"name": "High"}},
        "Type": {"select": {"name": "Follow-up"}},
        "Due Date": {"date": {"start": "2025-01-15"}}
    }
    if contact_id:
        prop["Contact"] = {"relation": [{"id": contact_id}]}

    children = [
        {"object": "block", "type": "callout", "callout": {"rich_text": [{"text": {"content": "This task will appear in your 'CRM Dashboard' under 'Notifications' if the due date is today!"}}], "icon": {"emoji": "🔔"}}}
    ]
    create_page(CRM_TASKS_DB_ID, prop, children)

def main():
    print("Seeding Notion Examples...")
    contact_id = get_first_contact_id()
    if contact_id:
        print(f"Found contact ID for linking: {contact_id}")
    else:
        print("No contacts found. Creating unlinked examples.")

    seed_wiki()
    seed_calendar()
    seed_meetings(contact_id)
    seed_tasks(contact_id)
    print("Done!")

if __name__ == "__main__":
    main()
