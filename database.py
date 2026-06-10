import os

# Try to load environment from .env if python-dotenv is installed, otherwise continue
try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    # No action needed; environment variables may be provided by the OS
    pass

# Try to import supabase; if unavailable, provide a lightweight in-memory fallback
USE_FAKE_SUPABASE = False
try:
    from supabase import create_client, Client  # type: ignore
except ModuleNotFoundError:
    USE_FAKE_SUPABASE = True


class _FakeTable:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._query = {}

    def select(self, *args, **kwargs):
        return self

    def ilike(self, field, pattern):
        # store the ilike query
        self._query[('ilike', field)] = pattern
        return self

    def eq(self, key, value):
        self._query[('eq', key)] = value
        return self

    def insert(self, record):
        # support chaining: .insert(...).execute()
        self._pending_insert = record.copy()
        return self

    def update(self, record):
        # prepare an update to be applied when execute() is called
        self._pending_update = record.copy()
        return self

    def execute(self):
        items = self.store.get(self.name, [])
        # If there's a pending insert, apply it now
        if hasattr(self, '_pending_insert'):
            self.store.setdefault(self.name, []).append(self._pending_insert)
            class _ResIns:
                data = [self._pending_insert]
            # clear pending
            del self._pending_insert
            return _ResIns()
        # If there's a pending update, apply it now
        if hasattr(self, '_pending_update'):
            updated = []
            for it in items:
                if it.get('phone_number') == self._query.get(('eq', 'phone_number')):
                    it.update(self._pending_update)
                    updated.append(it)
            del self._pending_update
            class _ResUpd:
                data = updated
            return _ResUpd()
        # apply eq filter
        eq_key, eq_val = None, None
        for (typ, key), val in self._query.items():
            if typ == 'eq':
                eq_key, eq_val = key, val
        results = items
        if eq_key is not None:
            results = [it for it in items if it.get(eq_key) == eq_val]

        # apply ilike filter if present
        for (typ, key), val in self._query.items():
            if typ == 'ilike':
                pattern = val.lower()
                results = [it for it in results if pattern.strip('%') in str(it.get(key, '')).lower()]

        class _Res:
            data = results
        return _Res()


class _FakeSupabase:
    def __init__(self):
        self._store = {
            'student_logs': [],
            'properties': [
                # sample data for local testing
                {'title': 'Senga Room A', 'price': 50, 'description': 'Near campus', 'location': 'Senga', 'available': True},
                {'title': 'Nehosho Flat', 'price': 70, 'description': 'Spacious', 'location': 'Nehosho', 'available': True},
            ]
        }

    def table(self, name):
        return _FakeTable(self._store, name)


if USE_FAKE_SUPABASE:
    supabase = _FakeSupabase()
else:
    # Initialize real Supabase Client
    url: str = os.getenv("SUPABASE_URL")
    key: str = os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "Environment variables SUPABASE_URL and SUPABASE_ANON_KEY must be set for real Supabase usage."
        )
    supabase: Client = create_client(url, key)  # type: ignore

def get_or_create_student(phone_number: str) -> str:
    """Checks if a student exists; if not, creates them and returns their state."""
    response = supabase.table("student_logs").select("last_state").eq("phone_number", phone_number).execute()
    
    if response.data:
        return response.data[0]["last_state"]
    else:
        # New user, insert them into the database
        supabase.table("student_logs").insert({"phone_number": phone_number, "last_state": "START"}).execute()
        return "START"

import httpx
import uuid

def get_or_create_student(phone_number: str) -> tuple:
    """Fetches user state AND their temporary form data progress."""
    response = supabase.table("student_logs").select("last_state", "temp_data").eq("phone_number", phone_number).execute()
    
    if response.data:
        state = response.data[0]["last_state"]
        temp_data = response.data[0].get("temp_data") or {}
        return state, temp_data
    else:
        # New user setup
        supabase.table("student_logs").insert({"phone_number": phone_number, "last_state": "START", "temp_data": {}}).execute()
        return "START", {}

def update_student_state(phone_number: str, new_state: str, temp_data: dict = None):
    """Updates the conversation step and preserves form progress memory."""
    payload = {"last_state": new_state}
    if temp_data is not None:
        payload["temp_data"] = temp_data
    supabase.table("student_logs").update(payload).eq("phone_number", phone_number).execute()

def get_or_create_landlord(phone_number: str) -> str:
    """Checks if landlord exists by phone; if not, signs them up automatically."""
    res = supabase.table("landlords").select("id").eq("phone_number", phone_number).execute()
    if res.data:
        return res.data[0]["id"]
    else:
        new_landlord = supabase.table("landlords").insert({"name": "WhatsApp Landlord", "phone_number": phone_number}).execute()
        return new_landlord.data[0]["id"]

def save_property(landlord_phone: str, temp_data: dict, image_url: str):
    """Compiles temporary form data and creates an official live property listing."""
    landlord_id = get_or_create_landlord(landlord_phone)
    supabase.table("properties").insert({
        "landlord_id": landlord_id,
        "title": temp_data.get("title"),
        "location": temp_data.get("location"),
        "price": float(temp_data.get("price", 0)),
        "description": temp_data.get("description"),
        "image_url": image_url
    }).execute()

def upload_media_to_supabase(twilio_media_url: str) -> str:
    """Downloads the photo from Twilio's servers and saves it permanently to Supabase Storage."""
    try:
        # Generate a completely unique filename so images never overwrite each other
        unique_filename = f"room_{uuid.uuid4()}.jpg"
        
        # Download image file bytes from Twilio
        with httpx.Client() as client:
            resp = client.get(twilio_media_url)
            if resp.status_code == 200:
                # Push raw bytes into your public Supabase Storage bucket
                bucket = supabase.storage.from_("property-images")
                bucket.upload(
                    path=unique_filename,
                    file=resp.content,
                    file_options={"content-type": "image/jpeg"}
                )
                # Fetch and return the permanent internet link to the file
                return bucket.get_public_url(unique_filename)
    except Exception as e:
        print(f"Storage upload failure: {e}")
    return None

def search_properties(location: str) -> list:
    """Finds verified, available rooms matching the location."""
    response = supabase.table("properties").select("*").ilike("location", location).eq("available", True).execute()
    return response.data