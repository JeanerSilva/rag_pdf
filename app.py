import streamlit as st
import openai
import os
import json
import hashlib
import time
from uuid import uuid4

# Inicializa o cliente OpenAI com a chave da API
client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# Define os caminhos para o PDF, arquivo de configuração do assistente e diretório de threads
PDF_PATH = "PDF/documento_ppa.pdf"
ASSISTANT_CONFIG_PATH = "assistant_config.json"
THREADS_DIR = "threads"
os.makedirs(THREADS_DIR, exist_ok=True)

# 🔐 Calcula o hash de um arquivo para verificar modificações
def hash_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

# 📌 Cria ou carrega o Assistant e associa o arquivo PDF através de um Vector Store
def get_or_create_assistant():
    current_hash = hash_file(PDF_PATH)

    # Tenta carregar a configuração existente do assistente
    if os.path.exists(ASSISTANT_CONFIG_PATH):
        try:
            with open(ASSISTANT_CONFIG_PATH, "r") as f:
                config = json.load(f)
            # Verifica se o PDF foi modificado ou se a configuração está incompleta
            if (config.get("pdf_hash") == current_hash and
                config.get("vector_store_id") and
                config.get("assistant_id") and
                config.get("file_id")): # Garante que file_id também existe
                
                # Tenta recuperar objetos para garantir que ainda são válidos na OpenAI
                try:
                    client.beta.assistants.retrieve(config["assistant_id"])
                    # Usa client.vector_stores para recuperação também
                    client.vector_stores.retrieve(config["vector_store_id"])
                    client.files.retrieve(config["file_id"])
                    return config["assistant_id"], config["file_id"], config["vector_store_id"]
                except openai.NotFoundError:
                    st.warning("Assistente, Vector Store ou Arquivo não encontrados na OpenAI. Recriando...")
                    os.remove(ASSISTANT_CONFIG_PATH) # Força a recriação se os objetos desapareceram

            else:
                # Se o PDF mudou ou a config está incompleta/corrompida, remove o arquivo de config para recriar
                st.warning("Configuração do assistente desatualizada ou incompleta. Recriando...")
                os.remove(ASSISTANT_CONFIG_PATH)
        except json.JSONDecodeError:
            st.warning("Arquivo de configuração do assistente corrompido. Recriando...")
            os.remove(ASSISTANT_CONFIG_PATH)
        except Exception as e:
            st.error(f"Erro inesperado ao carregar configuração do assistente: {e}. Recriando...")
            os.remove(ASSISTANT_CONFIG_PATH)


    # Se não existe config válida, cria tudo do zero

    st.info("Configurando o assistente pela primeira vez ou atualizando PDF. Isso pode levar alguns segundos...")

    # 1. Upload do arquivo PDF para a OpenAI
    with open(PDF_PATH, "rb") as f:
        uploaded_file = client.files.create(file=f, purpose="assistants")

    # 2. Cria um Vector Store (AGORA DIRETAMENTE SOB CLIENT, NÃO client.beta)
    vector_store = client.vector_stores.create(name="VectorStore-PPA")

    # 3. Adiciona o arquivo carregado ao Vector Store
    # Aguarda a conclusão do processamento do arquivo no Vector Store
    file_batch = client.vector_stores.file_batches.create(
        vector_store_id=vector_store.id,
        file_ids=[uploaded_file.id]
    )

    # Espera até que o batch de arquivos seja processado
    with st.spinner("Processando o documento no Vector Store..."):
        while True:
            # Usa client.vector_stores para recuperar o status do batch
            file_batch = client.vector_stores.file_batches.retrieve(
                vector_store_id=vector_store.id,
                batch_id=file_batch.id,
            )
            if file_batch.status in ["completed", "failed", "cancelled"]:
                break
            time.sleep(1) # Espera 1 segundo antes de tentar novamente

    if file_batch.status != "completed":
        st.error(f"Falha ao processar o arquivo no Vector Store. Status: {file_batch.status}. Por favor, tente novamente.")
        st.stop()

    # 4. Cria o Assistant, associando-o ao Vector Store (Assistants ainda está em beta)
    assistant = client.beta.assistants.create(
        name="Assistente do PPA",
        instructions=(
            "Você é um assistente de IA amigável e acessível, especializado no Programa Plurianual (PPA) do governo do Brasil. "
            "Sua missão é ajudar o cidadão a entender o PPA de forma simples, respondendo a perguntas **exclusivamente com base no documento PPA fornecido**. "
            "**Sua maior prioridade é diferenciar e apresentar claramente os 'Objetivos Estratégicos' e os 'Objetivos Específicos' quando solicitado.** "
            " - **Objetivos Estratégicos** são a visão de alto nível, os grandes propósitos ou direções. "
            " - **Objetivos Específicos** são os passos mais detalhados, concretos e mensuráveis para alcançar os objetivos estratégicos. "
            "Quando perguntado sobre 'objetivos estratégicos', forneça os pontos de alto nível. "
            "Quando perguntado sobre 'objetivos específicos', detalhe os resultados mais práticos e direcionados. "
            "Se o cidadão perguntar sobre 'objetivos' de forma geral, tente identificar se ele busca a visão ampla (estratégica) ou os detalhes (específicos) e responda de forma apropriada, talvez oferecendo ambos se a pergunta for ambígua. "
            "**Se a informação ou a distinção exata não estiver no documento, ou se você não conseguir diferenciar com clareza com base nele, diga explicitamente que a informação não foi encontrada ou não está clara no documento fornecido, evitando criar respostas ou 'alucinar'.** "
            "Seja sempre didático, claro e evite jargões técnicos. Mantenha as respostas concisas, mas completas para a pergunta."
        ),
        model="gpt-4-1106-preview",
        tools=[{"type": "file_search"}],
        tool_resources={
            "file_search": {
                "vector_store_ids": [vector_store.id]
            }
        }
    )

    # Salva os IDs do assistente, arquivo e vector store, junto com o hash do PDF
    with open(ASSISTANT_CONFIG_PATH, "w") as f:
        json.dump({
            "assistant_id": assistant.id,
            "file_id": uploaded_file.id,
            "vector_store_id": vector_store.id,
            "pdf_hash": current_hash
        }, f)

    return assistant.id, uploaded_file.id, vector_store.id


# 🧵 Cria ou carrega uma thread de conversação para o usuário
def get_or_create_thread():
    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid4()) # Gera um ID de usuário único se não existir
    thread_path = os.path.join(THREADS_DIR, f"{st.session_state.user_id}.json")
    if os.path.exists(thread_path):
        try:
            with open(thread_path, "r") as f:
                thread_id = json.load(f)["thread_id"]
                client.beta.threads.retrieve(thread_id) # Valida se a thread ainda existe na OpenAI
                return thread_id
        except (json.JSONDecodeError, openai.NotFoundError):
            st.warning("Thread de conversa corrompida ou não encontrada. Criando nova thread...")
            os.remove(thread_path) # Força a criação de uma nova thread
    
    thread = client.beta.threads.create() # Cria uma nova thread
    with open(thread_path, "w") as f:
        json.dump({"thread_id": thread.id}, f)
    return thread.id

# 💬 Exibe o histórico de mensagens da thread
def show_history(thread_id):
    messages = client.beta.threads.messages.list(thread_id=thread_id, order="asc") # Obtém mensagens em ordem ascendente
    for msg in messages.data:
        # Garante que o conteúdo é do tipo text e não outros tipos (ex: image_file)
        if msg.content and hasattr(msg.content[0], 'text') and msg.content[0].text:
            role = "👤 Cidadão" if msg.role == "user" else "🤖 Assistente"
            content = msg.content[0].text.value
            st.chat_message(role).markdown(content)

# 🚀 Início do aplicativo Streamlit
st.set_page_config(page_title="Assistente do PPA", page_icon="📄", layout="wide")
st.title("📄 Pergunte sobre o Programa Plurianual (PPA) do Governo")

# Dicas para o usuário final sobre como perguntar
st.markdown("### Dicas para fazer sua pergunta:")
st.markdown("- Tente perguntar sobre um tema específico, como 'Me fale sobre a saúde no PPA'.")
st.markdown("- Se quiser saber sobre os planos maiores, pergunte 'Quais são os **objetivos gerais** do PPA?'")
st.markdown("- Se quiser detalhes, pergunte 'Quais são os **objetivos específicos** para educação?'")
st.markdown("---") # Linha divisória para separar as dicas do chat

# Obtém ou cria o assistente e a thread de conversação
assistant_id, file_id, vector_store_id = get_or_create_assistant()
thread_id = get_or_create_thread()

# Exibe o histórico da conversa ao iniciar
show_history(thread_id)

# Campo de entrada para a pergunta do usuário
if user_input := st.chat_input("Digite sua pergunta sobre o PPA..."):
    st.chat_message("👤 Cidadão").markdown(user_input)

    # Adiciona a mensagem do usuário à thread (sem file_ids aqui, pois o assistente já tem acesso ao arquivo via vector store)
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_input,
    )

    # Cria uma "run" para que o assistente processe a mensagem e gere uma resposta
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=assistant_id
    )

    # Loop para aguardar a conclusão da "run"
    with st.spinner("Consultando o PPA..."):
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run_status.status == "completed":
                break
            elif run_status.status == "failed":
                # Mensagem de erro mais amigável para o cidadão
                st.error("Desculpe, não consegui encontrar uma resposta no documento do PPA para sua pergunta neste momento. Por favor, tente reformular sua pergunta ou perguntar sobre outro tópico.")
                if run_status.last_error: # Opcional: Para depuração, você pode querer ver o erro real no console/logs
                    print(f"Erro detalhado da OpenAI: {run_status.last_error.message}")
                st.stop()
            elif run_status.status == "requires_action":
                st.warning("O assistente requer uma ação. Funções de ferramenta podem ser necessárias (funcionalidade avançada não implementada nesta versão).")
                break
            time.sleep(1) # Espera um pouco antes de verificar novamente

        # Recupera as mensagens mais recentes e exibe a resposta do assistente
        messages = client.beta.threads.messages.list(thread_id=thread_id, order="desc")
        # Itera sobre as mensagens para encontrar a última do assistente
        for msg in messages.data:
            if msg.role == "assistant" and msg.content and hasattr(msg.content[0], 'text') and msg.content[0].text:
                st.chat_message("🤖 Assistente").markdown(msg.content[0].text.value)
                break # Sai do loop após encontrar e exibir a primeira mensagem do assistente
