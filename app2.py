import streamlit as st
import openai
import os
import json
import hashlib
import time
from uuid import uuid4

client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

PDF_PATH = "PDF/documento_ppa.pdf"
ASSISTANT_CONFIG_PATH = "assistant_config.json"
THREADS_DIR = "threads"
os.makedirs(THREADS_DIR, exist_ok=True)

def hash_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

def get_or_create_assistant():
    current_hash = hash_file(PDF_PATH)

    if os.path.exists(ASSISTANT_CONFIG_PATH):
        try:
            with open(ASSISTANT_CONFIG_PATH, "r") as f:
                config = json.load(f)
            if config.get("pdf_hash") == current_hash:
                return config["assistant_id"], config["file_id"]
            else:
                os.remove(ASSISTANT_CONFIG_PATH)
        except Exception:
            os.remove(ASSISTANT_CONFIG_PATH)

    # 1. Upload do PDF
    with open(PDF_PATH, "rb") as f:
        uploaded_file = client.files.create(file=f, purpose="assistants")

    # 2. Criar um vector store
    vector_store = client.beta.vector_stores.create(name="VectorStore-PPA")

    # 3. Adicionar arquivo ao vector store
    client.beta.vector_stores.file_batches.create(
        vector_store_id=vector_store.id,
        file_ids=[uploaded_file.id]
    )

    # 4. Criar o Assistant com o vector store
    assistant = client.beta.assistants.create(
        name="Assistente do PPA",
        instructions="Você responde perguntas com base no Programa Plurianual (PPA) do governo.",
        model="gpt-4-1106-preview",
        tools=[{"type": "file_search"}],
        tool_resources={
            "file_search": {
                "vector_store_ids": [vector_store.id]
            }
        }
    )

    with open(ASSISTANT_CONFIG_PATH, "w") as f:
        json.dump({
            "assistant_id": assistant.id,
            "file_id": uploaded_file.id,
            "vector_store_id": vector_store.id,
            "pdf_hash": current_hash
        }, f)

    return assistant.id, uploaded_file.id

def get_or_create_thread():
    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid4())
    thread_path = os.path.join(THREADS_DIR, f"{st.session_state.user_id}.json")
    if os.path.exists(thread_path):
        with open(thread_path, "r") as f:
            return json.load(f)["thread_id"]
    else:
        thread = client.beta.threads.create()
        with open(thread_path, "w") as f:
            json.dump({"thread_id": thread.id}, f)
        return thread.id

def show_history(thread_id):
    messages = client.beta.threads.messages.list(thread_id=thread_id)
    for msg in reversed(messages.data):
        role = "👤 Cidadão" if msg.role == "user" else "🤖 Assistente"
        content = msg.content[0].text.value
        st.chat_message(role).markdown(content)

# 🚀 Início da aplicação
st.set_page_config(page_title="Assistente do PPA", page_icon="📄", layout="wide")
st.title("📄 Pergunte sobre o Programa Plurianual (PPA) do Governo")

assistant_id, file_id = get_or_create_assistant()
thread_id = get_or_create_thread()
show_history(thread_id)

if user_input := st.chat_input("Digite sua pergunta sobre o PPA..."):
    st.chat_message("👤 Cidadão").markdown(user_input)

    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_input,
        file_ids=[file_id]  # reforça a relevância do PDF
    )

    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=assistant_id
    )

    with st.spinner("Consultando o PPA..."):
        while True:
            status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if status.status == "completed":
                break
            elif status.status == "failed":
                st.error("Erro ao gerar a resposta.")
                st.stop()
            time.sleep(1)

        messages = client.beta.threads.messages.list(thread_id=thread_id)
        last_msg = messages.data[0].content[0].text.value
        st.chat_message("🤖 Assistente").markdown(last_msg)
