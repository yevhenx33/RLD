
import os
import re

def main():
    # 1. Read the new markdown content
    md_path = "Paper/RLD v3 2c713cfc2b2280c49d61f783deee0955.md"
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 2. Process content for JS string
    # Replace image paths
    content = content.replace("RLD%20v3/", "/assets/paper/")
    content = content.replace("RLD v3/", "/assets/paper/")

    # Escape backslashes (must be done first)
    content = content.replace("\\", "\\\\")
    
    # Escape backticks
    content = content.replace("`", "\\`")
    
    # Escape ${ to prevent usage as variable
    content = content.replace("${", "\\${")

    # 3. Create the new post object
    new_post = f"""    {{
        id: 0,
        title: "Rate-Level Derivatives (RLD) v3: Whitepaper",
        date: "2026-01-08",
        category: "WHITEPAPER",
        summary: "The complete technical whitepaper for Rate-Level Derivatives, enabling on-chain synthetic bonds, volatility trading, and CDS markets.",
        readTime: "25 MIN READ",
        content: `
{content}
        `
    }},"""

    # 4. Read existing posts.js
    posts_js_path = "frontend/src/data/posts.js"
    with open(posts_js_path, "r", encoding="utf-8") as f:
        js_content = f.read()

    # 5. Insert new post
    # Find the start of the array
    # We look for "export const BLOG_POSTS = ["
    token = "export const BLOG_POSTS = ["
    if token in js_content:
        parts = js_content.split(token)
        # parts[0] is everything before
        # parts[1] is everything after
        new_js_content = parts[0] + token + "\n" + new_post + parts[1]
        
        # 6. Write back
        with open(posts_js_path, "w", encoding="utf-8") as f:
            f.write(new_js_content)
        print("Successfully updated posts.js")
    else:
        print("Could not find BLOG_POSTS array in file.")

if __name__ == "__main__":
    main()
