from google.genai import types
from google import genai

client = genai.Client(api_key="AIzaSyCKGxp618KvE9zTqqytuUCRa78FxE8wE3M")
with open('/Users/apoorvabhishek/Downloads/WhatsApp Image 2025-06-09 at 23.14.01.jpeg', 'rb') as f:
    image_bytes = f.read()

response = client.models.generate_content(
model='gemini-2.0-flash',
contents=[
    types.Part.from_bytes(
    data=image_bytes,
    mime_type='image/jpeg',
    ),
    """Extract the total amount, date, and platform from this transaction image. If available, also extract items purchased and the vendor name.
    Return the output in the following format:
    ```json
    {{
        "Amount": "Total amount",
        "Date": "Date of transaction",
        "Platform": "Platform used",
        "Items": "Items purchased",
        "Vendor": "Vendor name",
        "Note": "User note"
    }}
    ```"""
    ]
)
print(response.text)