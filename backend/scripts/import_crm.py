import os
import csv
import json
import requests
import time
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.getcwd(), '.env')
load_dotenv(dotenv_path)

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_VERSION = "2022-06-28"

if NOTION_API_KEY:
    print(f"Loaded Token: {NOTION_API_KEY[:4]}...{NOTION_API_KEY[-4:]} (Length: {len(NOTION_API_KEY)})")
else:
    print("No Token Loaded - check .env path")
    print(f"Tried loading from: {dotenv_path}")

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": NOTION_VERSION
}

def search_page(query):
    url = "https://api.notion.com/v1/search"
    payload = {
        "query": query,
        "filter": {
            "value": "page",
            "property": "object"
        }
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code == 200:
        results = response.json().get("results", [])
        if results:
            return results[0]["id"]
    return None

def create_database(parent_page_id):
    url = "https://api.notion.com/v1/databases"
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "Network CRM"}}],
        "properties": {
            "Name": {"title": {}},
            "Company": {"rich_text": {}},
            "Title": {"rich_text": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "Pending", "color": "gray"},
                        {"name": "In progress", "color": "blue"},
                        {"name": "Connected", "color": "green"},
                        {"name": "Done", "color": "green"}
                    ]
                }
            },
            "Priority": {
                "select": {
                    "options": [
                        {"name": "Low", "color": "gray"},
                        {"name": "Medium", "color": "blue"},
                        {"name": "High", "color": "orange"},
                        {"name": "Very High", "color": "red"}
                    ]
                }
            },
            "Category": {"multi_select": {}},
            "Telegram": {"url": {}},
            "Notes": {"rich_text": {}}
        }
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code == 200:
        return response.json()["id"]
    else:
        print(f"Error creating database: {response.text}")
        return None

def create_page(database_id, data):
    url = "https://api.notion.com/v1/pages"
    
    properties = {
        "Name": {"title": [{"text": {"content": data.get("Name", "") or "Unknown"}}]},
        "Company": {"rich_text": [{"text": {"content": data.get("Company", "") or ""}}]},
        "Title": {"rich_text": [{"text": {"content": data.get("Title", "") or ""}}]},
        "Notes": {"rich_text": [{"text": {"content": f"{data.get('Note', '')}\n\nFollow-up: {data.get('Follow-up?', '')}"}}]}
    }
    
    # Validate/Default Status
    status_val = data.get("Current Status")     
    if status_val and status_val.strip():
        status_val = status_val.strip()
    else:
        status_val = "Pending"
    
    properties["Status"] = {"select": {"name": status_val}}

    # Validate/Default Priority
    priority_val = data.get("Priority")
    if priority_val and priority_val.strip():
        priority_val = priority_val.strip()
    else:
        priority_val = "Low"
    
    properties["Priority"] = {"select": {"name": priority_val}}

    # Handle URL (Telegram)
    handle = data.get("Handle", "")
    if handle:
        if not handle.startswith("http") and not handle.startswith("@"):
             if handle.startswith("@"):
                 handle = f"https://t.me/{handle[1:]}"
             else:
                 if "." in handle and " " not in handle:
                     handle = f"https://{handle}"
                 else:
                     properties["Notes"]["rich_text"][0]["text"]["content"] += f"\n\nHandle: {handle}"
                     handle = None
        elif handle.startswith("@"):
             handle = f"https://t.me/{handle[1:]}"
             
        if handle:
            properties["Telegram"] = {"url": handle}

    # Handle Category
    resources = data.get("Resources", "")
    if resources:
        options = [r.strip() for r in resources.split(",") if r.strip()]
        if options:
            properties["Category"] = {"multi_select": [{"name": opt} for opt in options]}

    payload = {
        "parent": {"database_id": database_id},
        "properties": properties
    }
    
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code != 200:
        print(f"Error creating page for {data.get('Name')}: {response.text}")
        return False
    return True

def main():
    # 1. Use known Main Page ID
    parent_id = "2e8c2f1b-c76a-80b3-b758-e590a1544c4d"
    # parent_id = search_page("RLD Project")
    if not parent_id:
        print("Could not find 'RLD Project' page.")
        return

    print(f"Found Parent Page ID: {parent_id}")

    # 2. Create Database
    db_id = create_database(parent_id)
    if not db_id:
        print("Failed to create database.")
        return

    print(f"Created Database ID: {db_id}")

    # 3. Read CSV and Import
    csv_path = "Singapore 2025 Yevhen 28913cfc2b2280c094aedb80666777f9_all.csv"
    
    if not os.path.exists(csv_path):
        # search for it
        for file in os.listdir("."):
            if "Singapore" in file and "_all.csv" in file:
                csv_path = file
                break
    
    print(f"Reading from {csv_path}...")

    # Read CSV
    contacts = []
    seen_names = set()
    
    with open(csv_path, 'r', encoding='utf-8-sig') as f: # Use utf-8-sig to handle BOM
        reader = csv.DictReader(f)
        
        # Debug: Print first row keys
        if reader.fieldnames:
            print(f"CSV Headers: {reader.fieldnames}")
            
        for row in reader:
            name = row.get("Name", "").strip()
            if not name:
                continue
                
            if name in seen_names:
                print(f"Skipping duplicate: {name}")
                continue
            
            seen_names.add(name)
            contacts.append(row)
            
    print(f"Found {len(contacts)} unique contacts to import.")
    
    # 2. Create Database (Create NEW one to be sure)
    db_id = create_database(parent_id)
    if not db_id:
        print("Failed to create database.")
        return

    print(f"Created Database ID: {db_id}")

    count = 0
    success = 0
    for row in contacts:
        if create_page(db_id, row):
            success += 1
        count += 1
        time.sleep(0.4) 
        if count % 10 == 0:
            print(f"Processed {count} contacts...")

    print(f"Import Complete. {success}/{count} contacts imported.")

if __name__ == "__main__":
    main()
