import os
import datetime
import requests
import io
from webdav4.client import Client as WebDavClient

from flask import Flask, request, abort

# --- 环境变量部分保持不变 ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
AUTHORIZED_CHAT_ID = int(os.environ.get('AUTHORIZED_CHAT_ID', 0))
WEBDAV_HOSTNAME = os.environ.get('WEBDAV_HOSTNAME')
WEBDAV_USERNAME = os.environ.get('WEBDAV_USERNAME')
WEBDAV_PASSWORD = os.environ.get('WEBDAV_PASSWORD')
# 确保基础路径以 / 开头且不以 / 结尾
WEBDAV_BASE_PATH = os.environ.get('WEBDAV_BASE_PATH', '/').rstrip('/')
if not WEBDAV_BASE_PATH.startswith('/'):
    WEBDAV_BASE_PATH = '/' + WEBDAV_BASE_PATH

OBSIDIAN_ATTACHMENTS_FOLDER = os.environ.get('OBSIDIAN_ATTACHMENTS_FOLDER', 'attachments')

app = Flask(__name__)

# --- Favicon 处理，避免 Vercel 404 报错 ---
@app.route('/favicon.ico')
@app.route('/favicon.png')
def favicon():
    return '', 204

# --- WebDAV 客户端初始化 ---
# 坚果云的地址通常需要包含 /dav/ 前缀
webdav_url = f"https://{WEBDAV_HOSTNAME}"
if not webdav_url.endswith('/dav'):
    webdav_url = webdav_url.rstrip('/') + "/dav"

webdav_client = WebDavClient(
    base_url=webdav_url,
    auth=(WEBDAV_USERNAME, WEBDAV_PASSWORD)
)

# --- 新增辅助函数：发送 Telegram 回复消息 ---
def send_telegram_reply(chat_id, message_id, text):
    """
    向指定的 Telegram 聊天发送一条回复消息。
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'reply_to_message_id': message_id
    }
    try:
        response = requests.post(url, json=payload, timeout=5) # 设置5秒超时
        response.raise_for_status()
        print(f"Sent reply to chat_id {chat_id}: {text}")
    except requests.exceptions.RequestException as e:
        # 如果发送回复失败，只在日志中记录，不中断主流程
        print(f"Error sending Telegram reply: {e}")

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

# --- WebDAV 文件操作辅助函数 保持不变 ---
def create_webdav_folder_if_not_exists(folder_path):
    try:
        if not webdav_client.exists(folder_path):
            webdav_client.mkdir(folder_path)
            print(f"WebDAV folder created: {folder_path}")
        return True, ""
    except Exception as e:
        error_msg = str(e)
        print(f"Error checking/creating WebDAV folder {folder_path}: {error_msg}")
        return False, error_msg

def upload_file_to_webdav(file_content, webdav_full_path):
    try:
        file_obj = io.BytesIO(file_content)
        webdav_client.upload_fileobj(file_obj, webdav_full_path, overwrite=True)
        print(f"File uploaded to WebDAV: {webdav_full_path}")
        return True
    except Exception as e:
        print(f"Error uploading file to WebDAV {webdav_full_path}: {e}")
        return False

# --- 主处理函数 webhook (已更新，包含状态回复) ---
@app.route('/', methods=['GET', 'POST'])
@app.route('/api/index', methods=['GET', 'POST'])
def webhook():
    # --- 新增：处理 GET 请求以便用户在浏览器测试 ---
    if request.method == 'GET':
        return "✅ Telegram Obsidian Sync Bot is active. Please use this URL as your Webhook endpoint.", 200

    # 处理 Telegram 的 POST 请求
    update = request.get_json()
    if not update:
        return 'no content', 200
    
    message = update.get('message')
    if not message:
        return 'ok', 200

    chat_id = str(message['chat']['id'])
    message_id = message.get('message_id')

    try:
        # --- 安全检查 ---
        if chat_id != AUTHORIZED_CHAT_ID:
            print(f"Unauthorized access attempt from chat_id: {chat_id}")
            # 对于未授权的访问，不回复，直接中止
            abort(403)

        now = datetime.datetime.now()
        # 使用 message_id 保证文件名唯一性
        unique_id = message_id if message_id else now.strftime("%Y%m%d%H%M%S%f")
        note_filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{unique_id}.md"
        note_full_webdav_path = f"{WEBDAV_BASE_PATH}/{note_filename}"
        
        # --- 第一步：确保主文件夹存在 ---
        success_base, err_base = create_webdav_folder_if_not_exists(WEBDAV_BASE_PATH)
        if not success_base:
             # 如果是 405 错误，通常意味着文件夹已存在但 client.exists 没判断准，可以尝试继续
             print(f"Warning: Base folder check/create may have issues: {err_base}")

        # --- 第二步：确保附件文件夹存在 ---
        attachments_webdav_folder = f"{WEBDAV_BASE_PATH}/{OBSIDIAN_ATTACHMENTS_FOLDER}"
        success_att, err_att = create_webdav_folder_if_not_exists(attachments_webdav_folder)
        if not success_att:
             raise Exception(f"创建附件文件夹失败: {err_att}")

        note_content_parts = []
        
        # --- 处理图片 ---
        if 'photo' in message:
            # (省略了图片处理的详细代码，与上一版相同)
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
            image_filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{unique_id}.{image_extension}"
            image_full_webdav_path = f"{attachments_webdav_folder}/{image_filename}"
            
            if not upload_file_to_webdav(image_response.content, image_full_webdav_path):
                 raise Exception("上传图片文件失败")
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
        
        else:
             note_content_parts.append("收到一个非文本或图片的消息，暂未处理。\n")

        # --- 生成并上传最终笔记 ---
        if note_content_parts:
            final_note_content = "".join(note_content_parts)
            markdown_output = f"""
---
id: {unique_id}
date: {now.strftime('%Y-%m-%d %H:%M:%S')}
tags:
- telegram
- inbox
---

{final_note_content}"""
            if not upload_file_to_webdav(markdown_output.encode('utf-8'), note_full_webdav_path):
                raise Exception("上传笔记文件失败")

        # --- 所有操作成功，发送成功回复 ---
        send_telegram_reply(chat_id, message_id, "✅ 已同步到 Obsidian")
        return 'ok', 200

    except Exception as e:
        import traceback
        error_message = f"An unexpected error occurred: {e}"
        print(f"{error_message}\n{traceback.format_exc()}")
        # --- 发生错误，发送失败回复 ---
        send_telegram_reply(chat_id, message_id, f"❌ 同步失败: {e}")
        # 即使发送回复失败，也返回 200 OK，防止 Telegram 不断重试
        return 'ok', 200

# --- 本地测试入口 ---
if __name__ == '__main__':
    if not all([TELEGRAM_BOT_TOKEN, AUTHORIZED_CHAT_ID, WEBDAV_HOSTNAME, WEBDAV_USERNAME, WEBDAV_PASSWORD, WEBDAV_BASE_PATH]):
        print("Error: Missing one or more required environment variables for local testing.")
        print("Please set TELEGRAM_BOT_TOKEN, AUTHORIZED_CHAT_ID, WEBDAV_HOSTNAME, WEBDAV_USERNAME, WEBDAV_PASSWORD, WEBDAV_BASE_PATH.")
        exit(1)

    print("Running Flask app in debug mode locally. To test, use a tool like ngrok to expose it to the internet and set your Telegram webhook.")
    app.run(debug=True, port=5000)
