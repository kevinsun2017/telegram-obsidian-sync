import os
import datetime
import requests
import io # 需要导入 io 模块
from webdav4.client import Client as WebDavClient # 新的导入方式

from flask import Flask, request, abort

# --- 环境变量部分保持不变 ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
AUTHORIZED_CHAT_ID = os.environ.get('AUTHORIZED_CHAT_ID') 

WEBDAV_HOSTNAME = os.environ.get('WEBDAV_HOSTNAME')      
WEBDAV_USERNAME = os.environ.get('WEBDAV_USERNAME')      
WEBDAV_PASSWORD = os.environ.get('WEBDAV_PASSWORD')      
WEBDAV_BASE_PATH = os.environ.get('WEBDAV_BASE_PATH')    

OBSIDIAN_ATTACHMENTS_FOLDER = "attachments"

app = Flask(__name__)

# --- WebDAV 客户端初始化 (新方式) ---
webdav_client = WebDavClient(
    base_url=f"https://{WEBDAV_HOSTNAME}",
    auth=(WEBDAV_USERNAME, WEBDAV_PASSWORD)
)

# --- 格式化函数 format_telegram_text_to_markdown 保持不变 ---
def format_telegram_text_to_markdown(text, entities):
    if not text: return ""
    if not entities: return text
    entities.sort(key=lambda x: x['offset'])
    formatted_parts = []
    current_offset = 0
    text_utf16 = text.encode('utf-16-le')
    for entity in entities:
        offset, length, entity_type = entity['offset'], entity['length'], entity['type']
        if offset > current_offset:
            formatted_parts.append(text_utf16[current_offset*2 : offset*2].decode('utf-16-le'))
        entity_text = text_utf16[offset*2 : (offset + length)*2].decode('utf-16-le')
        if entity_type == 'bold': formatted_parts.append(f"**{entity_text}**")
        elif entity_type == 'italic': formatted_parts.append(f"*{entity_text}*")
        elif entity_type == 'code': formatted_parts.append(f"`{entity_text}`")
        elif entity_type == 'pre': formatted_parts.append(f"```{{entity.get('language', '')}}\n{{entity_text}}\n```")
        elif entity_type == 'text_link': formatted_parts.append(f"[{{entity_text}}]({{entity.get('url', '#')}})")
        elif entity_type == 'url': formatted_parts.append(f"<{{entity_text}}>")
        elif entity_type == 'strikethrough': formatted_parts.append(f"~~{{entity_text}}~~")
        elif entity_type == 'underline': formatted_parts.append(f"<ins>{{entity_text}}</ins>")
        elif entity_type == 'spoiler': formatted_parts.append(f"||{{entity_text}}||")
        else: formatted_parts.append(entity_text)
        current_offset = offset + length
    if current_offset*2 < len(text_utf16):
        formatted_parts.append(text_utf16[current_offset*2:].decode('utf-16-le'))
    return "".join(formatted_parts)

# --- WebDAV 文件操作辅助函数 (新方式) ---
def create_webdav_folder_if_not_exists(folder_path):
    try:
        if not webdav_client.exists(folder_path):
            webdav_client.mkdir(folder_path)
            print(f"WebDAV folder created: {folder_path}")
        return True
    except Exception as e:
        print(f"Error checking/creating WebDAV folder {folder_path}: {e}")
        return False

def upload_file_to_webdav(file_content, webdav_full_path):
    try:
        file_obj = io.BytesIO(file_content)
        webdav_client.upload_fileobj(file_obj, webdav_full_path, overwrite=True)
        print(f"File uploaded to WebDAV: {webdav_full_path}")
        return True
    except Exception as e:
        print(f"Error uploading file to WebDAV {webdav_full_path}: {e}")
        return False

# --- 主处理函数 webhook 保持不变 ---
@app.route('/api/index', methods=['POST'])
def webhook():
    if request.method != 'POST':
        abort(400)

    try:
        update = request.get_json()
        message = update.get('message')
        if not message:
            return 'ok', 200

        chat_id = str(message['chat']['id'])
        if chat_id != AUTHORIZED_CHAT_ID:
            print(f"Unauthorized access attempt from chat_id: {chat_id}")
            abort(403)

        now = datetime.datetime.now()
        message_id = message.get('message_id', now.strftime("%Y%m%d%H%M%S%f"))
        note_filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{message_id}.md"
        note_full_webdav_path = f"{WEBDAV_BASE_PATH}/{note_filename}"

        attachments_webdav_folder = f"{WEBDAV_BASE_PATH}/{OBSIDIAN_ATTACHMENTS_FOLDER}"
        create_webdav_folder_if_not_exists(attachments_webdav_folder)

        note_content_parts = []
        
        if 'photo' in message:
            photo = message['photo'][-1] 
            file_id = photo['file_id']
            caption = message.get('caption', '')
            caption_entities = message.get('caption_entities', [])

            telegram_file_info_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
            response = requests.get(telegram_file_info_url)
            response.raise_for_status()
            file_path = response.json()['result']['file_path']

            telegram_download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            image_response = requests.get(telegram_download_url)
            image_response.raise_for_status()
            
            image_extension = file_path.split('.')[-1] if '.' in file_path else "jpg"
            image_filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{message_id}.{image_extension}"
            image_full_webdav_path = f"{attachments_webdav_folder}/{image_filename}"
            
            upload_file_to_webdav(image_response.content, image_full_webdav_path)
            note_content_parts.append(f"![[{OBSIDIAN_ATTACHMENTS_FOLDER}/{image_filename}]]\n")
            
            if caption:
                formatted_caption = format_telegram_text_to_markdown(caption, caption_entities)
                note_content_parts.append(f"\n{formatted_caption}\n">

        elif 'text' in message:
            text_content = message['text']
            text_entities = message.get('entities', [])
            formatted_text = format_telegram_text_to_markdown(text_content, text_entities)
            note_content_parts.append(f"{formatted_text}\n")
        
        else:
             note_content_parts.append("收到一个非文本或图片的消息，暂未处理。\n")

        if note_content_parts:
            final_note_content = "".join(note_content_parts)
            markdown_output = f"""
---
id: {message_id}
date: {now.strftime('%Y-%m-%d %H:%M:%S')}
tags:
- telegram
- inbox
---

{final_note_content}"""
            upload_file_to_webdav(markdown_output.encode('utf-8'), note_full_webdav_path)

        return 'ok', 200

    except Exception as e:
        import traceback
        print(f"An unexpected error occurred: {e}\n{traceback.format_exc()}")
        abort(500, description="Internal Server Error. Check logs for details.")

# --- 本地测试入口 ---
if __name__ == '__main__':
    if not all([TELEGRAM_BOT_TOKEN, AUTHORIZED_CHAT_ID, WEBDAV_HOSTNAME, WEBDAV_USERNAME, WEBDAV_PASSWORD, WEBDAV_BASE_PATH]):
        print("Error: Missing one or more required environment variables for local testing.")
        print("Please set TELEGRAM_BOT_TOKEN, AUTHORIZED_CHAT_ID, WEBDAV_HOSTNAME, WEBDAV_USERNAME, WEBDAV_PASSWORD, WEBDAV_BASE_PATH.")
        exit(1)

    print("Running Flask app in debug mode locally. To test, use a tool like ngrok to expose it to the internet and set your Telegram webhook.")
    app.run(debug=True, port=5000)