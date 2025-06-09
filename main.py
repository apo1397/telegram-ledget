import os
import json
import requests
import base64
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Bot, Update
from telegram.ext import Dispatcher, MessageHandler, Filters # Make sure this line is uncommented
from google.oauth2 import service_account
import gspread
from google.genai import types
from google import genai
from typing import Dict, Tuple, Optional, Any

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# --- Environment Variable Checks (Optional but Recommended) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not GEMINI_API_KEY:
    print("Warning: GEMINI_API_KEY environment variable not set.")
# Add similar checks for TELEGRAM_BOT_TOKEN if that functionality is active

# --- Client Initializations ---
try:
    GeminiClient = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    print(f"Error initializing Gemini Client: {e}")
    GeminiClient = None # Handle cases where client might not initialize

# Initialize Bot (ensure this is uncommented and TELEGRAM_BOT_TOKEN is set)
if TELEGRAM_BOT_TOKEN:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
else:
    bot = None
    print("Warning: TELEGRAM_BOT_TOKEN not set. Telegram bot functionality will be disabled.")

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'google-creds.json'
SPREADSHEET_ID = '103kvUS9DPTsZB0bB5n7eI7XKm5ckvooGykx4IvpvkVI'

try:
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gspread_client = gspread.authorize(creds)
    sheet = gspread_client.open_by_key(SPREADSHEET_ID).sheet1
except Exception as e:
    print(f"Error initializing Google Sheets client: {e}")
    sheet = None # Handle cases where sheet might not initialize

def process_and_log_transaction(image_data: bytes, user_note: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Processes transaction image and text using Gemini, then logs to Google Sheets.

    Args:
        image_data: The byte content of the transaction image.
        user_note: A note provided by the user regarding the transaction.

    Returns:
        A tuple containing the processed data (dict) or an error message (dict), 
        and a boolean indicating success.
    """
    if not GeminiClient:
        return {"error": "Gemini client not initialized"}, False
    if not sheet:
        return {"error": "Google Sheets client not initialized"}, False

    fixed_prompt = """Extract the total amount, date, and platform from this transaction image. If available, also extract items purchased and the vendor name.
    Return the output in the following format:
    ```json
    {{
        "Amount": "Total amount",
        "Date": "Date of transaction",
        "Platform": "Platform used",
        "Items": "Items purchased",
        "Vendor": "Vendor name"
    }}
    ```"""

    try:
        response = GeminiClient.models.generate_content(
            model='gemini-2.0-flash', # Consider making model configurable
            contents=[
                types.Part.from_bytes(
                    data=image_data,
                    mime_type='image/jpeg', # Assuming JPEG, adjust if necessary
                ),
                fixed_prompt
            ]
        )
        
        cleaned_pre_analysis_text = response.text.strip()
        # Attempt to extract JSON from the response text
        json_start = cleaned_pre_analysis_text.find('{')
        json_end = cleaned_pre_analysis_text.rfind('}')
        
        if json_start != -1 and json_end != -1 and json_start < json_end:
            json_str = cleaned_pre_analysis_text[json_start:json_end+1]
            try:
                structured_analysis = json.loads(json_str)
                structured_analysis['Note'] = user_note # Add user note to the data
                print(f"Successfully parsed Gemini response: {json.dumps(structured_analysis, indent=2)}")

                # Log to Google Sheet
                # Ensure your Google Sheet has columns for: Amount, Date, Platform, Items, Vendor, Note, Raw Gemini Output
                sheet.append_row([
                    structured_analysis.get("Amount", ""),
                    structured_analysis.get("Date", ""),
                    structured_analysis.get("Platform", ""),
                    structured_analysis.get("Items", ""),
                    structured_analysis.get("Vendor", ""),
                    user_note,
                    response.text # Log the raw Gemini output for debugging/auditing
                ])
                return structured_analysis, True
            except json.JSONDecodeError as e:
                print(f"Failed to parse Gemini response as JSON: {e}")
                print(f"Cleaned text provided for parsing: {json_str}")
                print(f"Full response text: {response.text}")
                return {"error": "Failed to parse Gemini JSON response", "details": str(e), "raw_response": response.text}, False
        else:
            print("Could not find JSON object in the Gemini response.")
            print(f"Response text: {response.text}")
            return {"error": "Could not find JSON in Gemini response", "raw_response": response.text}, False

    except Exception as e:
        print(f"Error during Gemini API call or processing: {e}")
        return {"error": "Gemini API request failed", "details": str(e)}, False

# Handle photo message (Telegram bot)
def handle_image(update: Update, context):
    """Handles image messages sent to the Telegram bot."""
    if not bot:
        print("Telegram bot not initialized. Skipping handle_image.")
        if update.message:
             update.message.reply_text("Telegram bot is currently unavailable.")
        return

    try:
        # Get the largest photo and download its content
        photo_file = update.message.photo[-1].get_file()
        img_bytes = photo_file.download_as_bytearray()
        img_data = bytes(img_bytes)

    except Exception as e:
        print(f"Error downloading image from Telegram: {e}")
        update.message.reply_text("Sorry, I couldn't download the image.")
        return
    
    # The user's note is taken from the image caption
    telegram_user_note = update.message.caption if update.message.caption else "Sent via Telegram"
    
    # Call the main processing function
    processed_data, success = process_and_log_transaction(img_data, telegram_user_note)

    if success and processed_data:
        amount = processed_data.get('Amount', 'N/A')
        platform = processed_data.get('Platform', 'N/A')
        date = processed_data.get('Date', 'N/A')
        update.message.reply_text(f"Logged: {amount} on {platform} ({date}). Note: {telegram_user_note}")
    else:
        error_detail = processed_data.get('error', 'Unknown error') if processed_data else 'Unknown error'
        update.message.reply_text(f"Error processing image: {error_detail}")

# New API endpoint for the UI
@app.route("/api/process_transaction", methods=["POST"])
def api_process_transaction():
    """API endpoint to process a transaction image and note from a UI."""
    if 'image' not in request.files:
        return jsonify({"error": "No image file provided"}), 400
    if 'note' not in request.form:
        return jsonify({"error": "No note provided"}), 400

    image_file = request.files['image']
    user_note = request.form['note']
    
    if image_file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    try:
        image_bytes = image_file.read()
    except Exception as e:
        print(f"Error reading image file: {e}")
        return jsonify({"error": "Could not read image file", "details": str(e)}), 400

    processed_data, success = process_and_log_transaction(image_bytes, user_note)

    if success:
        return jsonify({"message": "Transaction processed successfully", "data": processed_data}), 200
    else:
        # Ensure processed_data (which is an error dict here) is serializable
        return jsonify({"error": "Failed to process transaction", "details": processed_data}), 500

# Setup dispatcher for Telegram (Uncomment these lines)
if bot:
    dispatcher = Dispatcher(bot, None, workers=0) # Consider adjusting workers based on load
    # This handler will call `handle_image` when a photo message is received
    dispatcher.add_handler(MessageHandler(Filters.photo, handle_image))
else:
    dispatcher = None

# Webhook route for Telegram (Uncomment this section)
@app.route(f"/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    """Webhook endpoint for Telegram to send updates."""
    if not dispatcher or not WEBHOOK_SECRET:
        print("Webhook not configured or dispatcher not available.")
        return "Webhook not configured", 500 # Or a 404 if you prefer to hide it
    
    # Process the update from Telegram
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok", 200

if __name__ == '__main__':
    # For local development.
    # Ensure GEMINI_API_KEY, (optionally TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET) are set as environment variables.
    # And 'google-creds.json' is in the same directory.
    # For the webhook to work, your Flask app needs to be accessible from the internet.
    # You'll need to set the webhook URL with Telegram using their API.
    # Example (run this once, perhaps in a separate script or manually via curl after your app is deployed):
    if bot and WEBHOOK_SECRET:
        # Replace YOUR_PUBLIC_URL with the actual public URL of your app
        # e.g., https://your-app-name.onrender.com or your ngrok URL for testing
        webhook_url = f"https://8248-122-172-81-107.ngrok-free.app/{WEBHOOK_SECRET}"
        bot.set_webhook(webhook_url)
        print(f"Telegram webhook set to: {webhook_url}")
    
    app.run(debug=True, port=5001)

