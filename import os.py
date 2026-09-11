import os
# pyrefly: ignore [missing-import]
import requests
# pyrefly: ignore [missing-import]
from bs4 import BeautifulSoup
# pyrefly: ignore [missing-import]
from langchain_text_splitters import RecursiveCharacterTextSplitter
# pyrefly: ignore [missing-import]
import chromadb
# pyrefly: ignore [missing-import]
from sentence_transformers import SentenceTransformer

# 1. Initialize local persistent ChromaDB and embedder
chroma_client = chromadb.PersistentClient(path="./sathyabama_chroma_db")
collection = chroma_client.get_or_create_collection(name="sathyabama_knowledge_base")
embedder = SentenceTransformer("all-MiniLM-L6-v2")

def scrape_page(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.extract()
            text = soup.get_text(separator="\n")
            lines = (line.strip() for line in text.splitlines())
            return "\n".join(chunk for chunk in lines if chunk)
    except Exception as e:
        print(f"Error scraping {url}: {e}")
    return ""

urls_to_scrape = [
    "https://www.sathyabama.ac.in/",
    "https://www.sathyabama.ac.in/academics",
    "https://www.sathyabama.ac.in/admissions"
]

print("Ingesting Sathyabama web pages...")
text_splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=120)
doc_id = 0

for url in urls_to_scrape:
    print(f"Reading: {url}")
    content = scrape_page(url)
    if not content:
        continue
    
    chunks = text_splitter.split_text(content)
    for chunk in chunks:
        doc_id += 1
        embedding = embedder.encode(chunk).tolist()
        collection.upsert(
            ids=[f"chunk_{doc_id}"],
            embeddings=[embedding],
            documents=[chunk],
            metadatas=[{"source": url}]
        )

print(f"Done! {doc_id} chunks stored inside './sathyabama_chroma_db'.")
