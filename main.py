from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse
import database as db

app = FastAPI()

@app.post("/whatsapp")
@app.post("/whatsapp")
def whatsapp_webhook(
    Body: str = Form(""),         # 🌟 CHANGED from ... to "" to make text optional for images!
    From: str = Form(...),
    NumMedia: int = Form(0),      # Detects how many files are attached
    MediaUrl0: str = Form(None)   # Links directly to the file attachment link
):
    user_msg = Body.strip()
    phone_number = From  # Format: 'whatsapp:+263xxxxxxxxx'
    
    # 1. Fetch conversational state AND structural memory dictionary
    current_state, temp_data = db.get_or_create_student(phone_number)
    
    twilio_resp = MessagingResponse()

    # ==================== GENERAL ROUTING TREE ====================
    if current_state == "START":
        msg = twilio_resp.message()
        clean_msg = user_msg.lower()
        if "hello" in clean_msg or "hi" in clean_msg or "menu" in clean_msg:
            msg.body(
                "Welcome to CampusVerify! 🏡\nHelp us combat rental scams.\n\n"
                "Reply with *1* to Search for Accommodation\n"
                "Reply with *2* to List/ADD Your Property Asset."
            )
            db.update_student_state(phone_number, "CHOOSING_ROLE", {})
        else:
            msg.body("Hi there! Send 'Hi' or 'Menu' to view options.")

    elif current_state == "CHOOSING_ROLE":
        msg = twilio_resp.message()
        if user_msg == "1":
            msg.body("Great! Which location are you looking for?\n(e.g., Senga, Nehosho, Daylesford)")
            db.update_student_state(phone_number, "AWAITING_LOCATION", {})
        elif user_msg == "2":
            msg.body("Let's start your listing! 📝\nWhat is the *Title* of your accommodation?\n(e.g., Twin house , Blue house , Superb Res)")
            db.update_student_state(phone_number, "LANDLORD_AWAITING_TITLE", {})
        else:
            msg.body("Invalid choice. Please reply with *1* (Student) or *2* (Landlord).")

    # ==================== STUDENT ACCOMMODATION SEARCH ====================
    elif current_state == "AWAITING_LOCATION":
        location_query = user_msg.capitalize()
        properties = db.search_properties(location_query)
        
        if properties:
            header_msg = twilio_resp.message()
            header_msg.body(f"📍 *Available Rooms in {location_query}:*\n")
            
            for prop in properties:
                prop_msg = twilio_resp.message()
                body_text = f"🏠 *{prop['title']}*\n💵 Price: ${prop['price']}\n📝 Details: {prop['description']}"
                prop_msg.body(body_text)
                if prop.get('image_url'):
                    prop_msg.media(prop['image_url'])
                    
            footer_msg = twilio_resp.message()
            footer_msg.body("Reply 'Hi' to start a new search.")
        else:
            fallback_msg = twilio_resp.message()
            fallback_msg.body(f"Sorry, no verified rooms found in '{location_query}'. Reply 'Hi' to try another area.")
        
        db.update_student_state(phone_number, "START", {})

    # ==================== LANDLORD ONBOARDING FLOW ====================
    elif current_state == "LANDLORD_AWAITING_TITLE":
        temp_data["title"] = user_msg
        msg = twilio_resp.message()
        msg.body("Got it. Which *Sub-locality/Area* is this located in?\n(e.g., Senga, Nehosho, Daylesford)")
        db.update_student_state(phone_number, "LANDLORD_AWAITING_LOCATION", temp_data)

    elif current_state == "LANDLORD_AWAITING_LOCATION":
        temp_data["location"] = user_msg.capitalize()
        msg = twilio_resp.message()
        msg.body("What is the *Monthly Rent Price* in USD? (Just enter the digits, e.g., 65)")
        db.update_student_state(phone_number, "LANDLORD_AWAITING_PRICE", temp_data)

    elif current_state == "LANDLORD_AWAITING_PRICE":
        # Basic validation ensuring numeric digits are entered
        if not user_msg.replace('.', '', 1).isdigit():
            msg = twilio_resp.message()
            msg.body("Please type numbers only. What is the monthly rent price?")
            return Response(content=str(twilio_resp), media_type="application/xml")
            
        temp_data["price"] = user_msg
        msg = twilio_resp.message()
        msg.body("Please type a short *Description* of key inclusions:\n(e.g., Tiled room, borehole water, security gate)")
        db.update_student_state(phone_number, "LANDLORD_AWAITING_DESCRIPTION", temp_data)

    elif current_state == "LANDLORD_AWAITING_DESCRIPTION":
        temp_data["description"] = user_msg
        msg = twilio_resp.message()
        msg.body("📸 Excellent! Finally, please *Upload/Send a Photo* of the room right here on WhatsApp.")
        db.update_student_state(phone_number, "LANDLORD_AWAITING_IMAGE", temp_data)

    elif current_state == "LANDLORD_AWAITING_IMAGE":
        msg = twilio_resp.message()
        
        # Verify if an attachment file actually arrived in the webhook payload
        if NumMedia > 0 and MediaUrl0:
            # 1. Run storage pipeline script to save image to bucket
            public_image_link = db.upload_media_to_supabase(MediaUrl0)
            
            if public_image_link:
                # 2. Add complete data object directly to property matrix
                db.save_property(phone_number, temp_data, public_image_link)
                
                msg.body("🎉 Success! Your property listing has been successfully saved and is now live for student discovery queries. Thank you for protecting our community!")
                db.update_student_state(phone_number, "START", {}) # Wipe temp memory clean
            else:
                msg.body("⚠️ Image processing failed. Please try sending the image link or file attachment again.")
        else:
            msg.body("No photo detected. Please attach a real image file using the clip attachment icon on WhatsApp.")

    return Response(content=str(twilio_resp), media_type="application/xml")