import os
import traceback
import sqlite3
import random
import string
# pyrefly: ignore [missing-import]
from fastapi import FastAPI, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
# pyrefly: ignore [missing-import]
import chromadb
# pyrefly: ignore [missing-import]
from sentence_transformers import SentenceTransformer
from google import genai

app = FastAPI(title="SIA Backend")

# Enable full CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. ChromaDB Vector Store
DB_PATH = os.path.join(os.path.dirname(__file__), "sathyabama_chroma_db")
chroma_client = chromadb.PersistentClient(path=DB_PATH)
collection = chroma_client.get_or_create_collection(name="sathyabama_knowledge_base")
embedder = SentenceTransformer("all-MiniLM-L6-v2")

# 2. Gemini Client (Reads GEMINI_API_KEY from environment or default credentials)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else genai.Client()

SYSTEM_PROMPT = """
You are SIA (Stream AI), the official Sathyabama Academic & Campus Navigation Assistant developed by Team 16 for Sathyabama Institute of Science and Technology.

1. ZERO HALLUCINATION POLICY:
Strictly base your answers on the provided [CAMPUS CONTEXT]. If details are absent, reply:
"I do not find that official record in the current verified circulars. Please check with the Academic Office or relevant department desk."

2. MULTILINGUAL SUPPORT:
- Auto-detect query language.
- Reply in English for English queries, Tamil (தமிழ்) for Tamil queries, and Hindi (हिन्दी) for Hindi queries.

3. GPS BUTTON INJECTION:
When mentioning campus venues, inject this HTML button template:
<button onclick="openMap(LAT, LNG)" class="text-blue-600 underline font-bold cursor-pointer inline-flex items-center gap-1">Open [Location Name] 📍</button>

Geocodes:
- Campus Main Gate: 12.8742, 80.2226
- Central Exam Hall 1: 12.8743, 80.2225
- Block A (CSE/IT): 12.8745, 80.2230
- Block B (ECE/EEE): 12.8740, 80.2224
- Dr. Remi Bai Central Library: 12.8738, 80.2220
- Free Student Dining Mess: 12.8752, 80.2232
- Dr. APJ Abdul Kalam Auditorium: 12.8746, 80.2227
- Jeppiaar Boys Hostel: 12.8730, 80.2214
- Maria Girls Hostel: 12.8725, 80.2208
"""

# =====================================================================
# Database Helper (Handles Locks Gracefully)
# =====================================================================
SQLITE_DB = os.path.join(os.path.dirname(__file__), "sathyabama_students.db")

def get_db_connection():
    conn = sqlite3.connect(SQLITE_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_sqlite_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            reg_no TEXT UNIQUE NOT NULL,
            department TEXT NOT NULL,
            batch TEXT NOT NULL,
            student_id TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("SELECT COUNT(*) FROM announcements")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO announcements (message) VALUES (?)", 
                       ("📢 Welcome to Sathyabama AI Portal! Check the GPS directory for campus navigation.",))
    conn.commit()
    conn.close()

init_sqlite_db()

# =====================================================================
# Pydantic Schemas
# =====================================================================
class UserQuery(BaseModel):
    query: str

class StudentCreate(BaseModel):
    name: str
    reg_no: str
    department: str
    batch: str

class AnnouncementCreate(BaseModel):
    message: str

def generate_random_password(length=8):
    chars = string.ascii_letters + string.digits + "@#$"
    return ''.join(random.choice(chars) for _ in range(length))

# =====================================================================
# Endpoints
# =====================================================================

@app.post("/api/chat")
async def chat_handler(data: UserQuery):
    try:
        query_text = data.query.strip()
        if not query_text:
            raise HTTPException(status_code=400, detail="Query cannot be empty.")

        query_vector = embedder.encode(query_text).tolist()
        search_results = collection.query(query_embeddings=[query_vector], n_results=4)
        
        docs = search_results.get("documents", [[]])
        retrieved_docs = docs[0] if docs else []
        campus_context = "\n---\n".join(retrieved_docs) if retrieved_docs else "No specific documents found."

        full_prompt = f"{SYSTEM_PROMPT}\n\n[CAMPUS CONTEXT]:\n{campus_context}\n\n[STUDENT QUERY]:\n{query_text}"

        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=full_prompt,
        )
        return {"reply": response.text}
    except Exception as e:
        print("\n--- CHAT ERROR ---")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/register-student")
async def register_student(student: StudentCreate):
    print(f"\n[ADMIN] Registering Student: {student.name}, Reg: {student.reg_no}")
    conn = get_db_connection()
    cursor = conn.cursor()

    random_code = random.randint(1000, 9999)
    new_student_id = f"SBU-{student.batch}-{random_code}"
    new_password = generate_random_password(8)

    try:
        cursor.execute("""
            INSERT INTO students (name, reg_no, department, batch, student_id, password)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (student.name, student.reg_no, student.department, student.batch, new_student_id, new_password))
        conn.commit()
        print(f"[ADMIN] Success! ID: {new_student_id}")
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="A student with this Register Number already exists.")
    except Exception as e:
        conn.close()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

    return {
        "status": "success",
        "name": student.name,
        "reg_no": student.reg_no,
        "student_id": new_student_id,
        "password": new_password
    }

@app.get("/api/announcement")
async def get_latest_announcement():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT message FROM announcements ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return {"message": row["message"] if row else "No current announcements."}

@app.post("/api/admin/announcement")
async def post_announcement(data: AnnouncementCreate):
    print(f"\n[ADMIN] Publishing Announcement: {data.message}")
    msg = data.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Announcement message cannot be empty.")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO announcements (message) VALUES (?)", (msg,))
        conn.commit()
        print("[ADMIN] Announcement published successfully.")
    except Exception as e:
        conn.close()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
        
    return {"status": "success", "message": msg}

if __name__ == "__main__":
    # pyrefly: ignore [missing-import]
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)   