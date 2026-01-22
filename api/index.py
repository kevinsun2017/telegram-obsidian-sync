import os
import datetime
import requests
import webdav.client as wc
import json 

from flask import Flask, request, abort

# --- 从环境变量读取敏感信息 ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
AUTHORIZED_CHAT_ID = os.environ.get('AUTHORIZED_CHAT_ID') 

# 坚果云 WebDAV 配置
WEBDAV_HOSTNAME = os.environ.get('WEBDAV_HOSTNAME')      
WEBDAV_USERNAME = os.environ.get('WEBDAV_USERNAME')      
WEBDAV_PASSWORD = os.environ.get('WEBDAV_PASSWORD')      
WEBDAV_BASE_PATH = os.environ.get('WEBDAV_BASE_PATH')    

# Obsidian 附件文件夹名 (相对于 WEBDAV_BASE_PATH)
OBSIDIAN_ATTACHMENTS_FOLDER = "attachments"

app = Flask(__name__)

# --- WebDAV 客户端初始化 ---
webdav_client = wc.Client({
    'webdav_hostname': f"https://{WEBDAV_HOSTNAME}",
    'webdav_login': WEBDAV_USERNAME,
    'webdav_password': WEBDAV_PASSWORD
})

# --- 辅助函数：将 Telegram entities 转换为 Markdown ---
def format_telegram_text_to_markdown(text, entities):
    """
    将 Telegram 消息文本和 entities 转换为 Obsidian 兼容的 Markdown 格式。
    """
    if not text:
        return ""
    if not entities:
        return text
    
    entities.sort(key=lambda x: x['offset'])

    formatted_parts = []
    current_offset = 0

    # 将文本转换为UTF-16码元列表，以便正确处理offset和length
    text_utf16 = text.encode('utf-16-le')

    for entity in entities:
        offset = entity['offset']
        length = entity['length']
        entity_type = entity['type']

        # 添加实体前的普通文本
        if offset > current_offset:
            formatted_parts.append(text_utf16[current_offset*2 : offset*2].decode('utf-16-le'))

        # 提取实体文本
        entity_text = text_utf16[offset*2 : (offset + length)*2].decode('utf-16-le')
        
        # 应用Markdown格式
        if entity_type == 'bold':
            formatted_parts.append(f"**{entity_text}**")
        elif entity_type == 'italic':
            formatted_parts.append(f"*{entity_text}*")
        elif entity_type == 'code':
            formatted_parts.append(f"`{entity_text}`")
        elif entity_type == 'pre': 
            lang = entity.get('language', '')
            formatted_parts.append(f"```{lang}\n{entity_text}\n```")
        elif entity_type == 'text_link': 
            url = entity.get('url', '#')
            formatted_parts.append(f"[{entity_text}]({url})")
        elif entity_type == 'url': 
            formatted_parts.append(f"<{entity_text}>")
        elif entity_type == 'strikethrough':
            formatted_parts.append(f"~~{entity_text}~~")
        elif entity_type == 'underline':
            formatted_parts.append(f"<ins>{entity_text}</ins>")
        elif entity_type == 'spoiler':
             formatted_parts.append(f"||{entity_text}||")
        else:
            formatted_parts.append(entity_text)
        
        current_offset = offset + length

    # 添加最后一个实体后的普通文本
    if current_offset*2 < len(text_utf16):
        formatted_parts.append(text_utf16[current_offset*2:].decode('utf-16-le'))

    return "".join(formatted_parts)

# --- WebDAV 文件操作辅助函数 ---
def create_webdav_folder_if_not_exists(folder_path):
    """
    在 WebDAV 服务器上创建文件夹，如果它不存在的话。
    """
    try:
        if not webdav_client.check(folder_path):
            webdav_client.mkdir(folder_path)
            print(f"WebDAV folder created: {folder_path}")
        return True
    except Exception as e:
        print(f"Error checking/creating WebDAV folder {folder_path}: {e}")
        return False

def upload_file_to_webdav(file_content, webdav_full_path):
    """
    将文件内容上传到 WebDAV 服务器。
    """
    try:
        webdav_client.upload_to(remote_path=webdav_full_path, data=file_content, overwrite=True)
        print(f"File uploaded to WebDAV: {webdav_full_path}")
        return True
    except Exception as e:
        print(f"Error uploading file to WebDAV {webdav_full_path}: {e}")
        return False

# --- Telegram Bot Webhook 主处理函数 ---
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
        
        # --- 处理图片 ---
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
                note_content_parts.append(f"\n{formatted_caption}\n")

        # --- 处理文本 ---
        elif 'text' in message:
            text_content = message['text']
            text_entities = message.get('entities', [])
            formatted_text = format_telegram_text_to_markdown(text_content, text_entities)
            note_content_parts.append(f"{formatted_text}\n")
        
        # --- 处理其他类型 (简化) ---
        else:
             note_content_parts.append("收到一个非文本或图片的消息，暂未处理。\n")

        # --- 生成并上传最终笔记 ---
        if note_content_parts:
            final_note_content = "".join(note_content_parts)
            markdown_output = f"""---
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
# 仅用于本地开发和测试，Vercel 部署时不会运行此段
if __name__ == '__main__':
    # 💡 提示：本地测试时，你可以创建一个 .env 文件，在其中定义所有环境变量，
    # 并使用 python-dotenv 库来加载它们。
    
    # from dotenv import load_dotenv
    # load_dotenv() # 加载 .env 文件中的变量
    
    # ⚠️ 确保所有环境变量都已设置，否则会报错
    if not all([TELEGRAM_BOT_TOKEN, AUTHORIZED_CHAT_ID, WEBDAV_HOSTNAME, WEBDAV_USERNAME, WEBDAV_PASSWORD, WEBDAV_BASE_PATH]):
        print("Error: Missing one or more required environment variables for local testing.")
        print("Please set TELEGRAM_BOT_TOKEN, AUTHORIZED_CHAT_ID, WEBDAV_HOSTNAME, WEBDAV_USERNAME, WEBDAV_PASSWORD, WEBDAV_BASE_PATH.")
        exit(1)

    print("Running Flask app in debug mode locally. To test, use a tool like ngrok to expose it to the internet and set your Telegram webhook.")
    app.run(debug=True, port=5000)
