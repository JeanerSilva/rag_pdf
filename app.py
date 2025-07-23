import streamlit as st
import openai
import os
import json
import hashlib
import time
from uuid import uuid4
import shutil

# Inicializa o cliente OpenAI com a chave da API
client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# Define os caminhos para o PDF, arquivo de configuração do assistente e diretório de threads
#PDF_PATH = "PDF/documento_ppa.pdf"
PDF_FOLDER = "PDF"
ASSISTANT_CONFIG_PATH = "assistant_config.json"
THREADS_DIR = "threads"
os.makedirs(THREADS_DIR, exist_ok=True)

def reset_config():
    if os.path.exists(ASSISTANT_CONFIG_PATH):
        os.remove(ASSISTANT_CONFIG_PATH)
        st.info("❌ Arquivo de configuração do assistente apagado.")
    if os.path.exists(THREADS_DIR):
        shutil.rmtree(THREADS_DIR)
        st.info("🧹 Pasta de threads apagada.")
    os.makedirs(THREADS_DIR, exist_ok=True)
    st.success("Configuração reiniciada. A página será recarregada.")
    time.sleep(1.5)
    st.experimental_rerun()

# 🔐 Calcula o hash de um arquivo para verificar modificações
def hash_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

# 📌 Cria ou carrega o Assistant e associa o arquivo PDF através de um Vector Store
def get_or_create_assistant():
    PDF_FOLDER = "PDF"
    os.makedirs(PDF_FOLDER, exist_ok=True)

    DEBUG_MODE = st.sidebar.checkbox("🔍 Modo Debug")

    def hash_file(path):
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    # 1. Lê todos os PDFs da pasta e calcula os hashes
    pdf_hashes = {}
    local_pdfs = []

    st.markdown("### 📂 Arquivos encontrados na pasta:")
    for filename in os.listdir(PDF_FOLDER):
        path = os.path.join(PDF_FOLDER, filename)
        if not os.path.isfile(path):
            continue

        try:
            with open(path, "rb") as f:
                _ = f.read(512)  # Verifica se é legível e acessível
            pdf_hashes[filename] = hash_file(path)
            local_pdfs.append(path)
            st.markdown(f"- ✅ `{filename}` pronto para envio")
        except Exception as e:
            st.markdown(f"- ❌ `{filename}` ignorado (erro ao ler): {e}")

    # 2. Tenta carregar configuração existente
    if os.path.exists(ASSISTANT_CONFIG_PATH):
        try:
            with open(ASSISTANT_CONFIG_PATH, "r") as f:
                config = json.load(f)

            if (
                config.get("pdf_hashes") == pdf_hashes and
                config.get("vector_store_id") and
                config.get("assistant_id") and
                config.get("file_ids")
            ):
                try:
                    client.beta.assistants.retrieve(config["assistant_id"])
                    client.vector_stores.retrieve(config["vector_store_id"])

                    # Verifica se arquivos ainda existem na OpenAI
                    for fid in config["file_ids"]:
                        file_info = client.files.retrieve(fid)
                        if DEBUG_MODE:
                            st.text(f"📂 {file_info.filename} | criado em {file_info.created_at}")
                    return config["assistant_id"], config["file_ids"], config["vector_store_id"]
                except openai.NotFoundError:
                    st.warning("Algum objeto na OpenAI não foi encontrado. Recriando tudo.")
                    os.remove(ASSISTANT_CONFIG_PATH)
        except Exception as e:
            st.warning(f"Erro ao carregar configuração existente: {e}. Recriando...")
            os.remove(ASSISTANT_CONFIG_PATH)

    st.info("Configurando o assistente com múltiplos PDFs... Isso pode levar alguns segundos.")
    start_time = time.time()

    # 3. Upload dos PDFs com mensagens específicas
    uploaded_files = []
    for path in local_pdfs:
        try:
            with open(path, "rb") as f:
                uploaded = client.files.create(file=f, purpose="assistants")
                uploaded_files.append(uploaded)
        except Exception as e:
            st.error(f"❌ Erro ao enviar `{os.path.basename(path)}`: {e}")

    if not uploaded_files:
        st.error("Nenhum arquivo foi enviado com sucesso. Verifique a pasta PDF.")
        st.stop()

    # 4. Exibe lista de arquivos carregados
    st.markdown("### 📚 Documentos carregados:")
    for file in uploaded_files:
        st.markdown(f"- `{file.filename}`")

    if DEBUG_MODE:
        st.markdown("### 🛠️ Debug - Hashes e IDs")
        for i, file in enumerate(uploaded_files):
            filename = file.filename
            st.code(f"{filename}\nHash: {pdf_hashes.get(filename)}\nFile ID: {file.id}")

    # 5. Criação do Vector Store
    vector_store = client.vector_stores.create(name="VectorStore-PPA")

    # 6. Adiciona arquivos ao Vector Store
    file_batch = client.vector_stores.file_batches.create(
        vector_store_id=vector_store.id,
        file_ids=[f.id for f in uploaded_files]
    )

    with st.spinner("Processando os documentos no Vector Store..."):
        while True:
            file_batch = client.vector_stores.file_batches.retrieve(
                vector_store_id=vector_store.id,
                batch_id=file_batch.id
            )
            if file_batch.status in ["completed", "failed", "cancelled"]:
                break
            time.sleep(1)

    if file_batch.status != "completed":
        st.error(f"❌ Falha ao processar os arquivos. Status: {file_batch.status}")
        st.stop()

    # 7. Cria o Assistente
    assistant = client.beta.assistants.create(
        name="Assistente do PPA",
        instructions=(
            "Você é um assistente de IA amigável e acessível, especializado no Programa Plurianual (PPA) do governo do Brasil e presta informação ao cidadão leigo no assunto."
            "Sua missão é ajudar o cidadão a entender o PPA de forma simples, respondendo a perguntas **exclusivamente com base no documento PPA fornecido**. "
            "**Se a informação ou a distinção exata não estiver no documento, ou se você não conseguir diferenciar com clareza com base nele, diga explicitamente que a informação não foi encontrada ou não está clara no documento fornecido, evitando criar respostas ou 'alucinar'.** "
            "Seja sempre didático, claro e evite jargões técnicos. Mantenha as respostas concisas, mas completas para a pergunta."
            "Quando demandado a informar algo como 'quais são os' liste todos existentes e não apenas alguns exemplos"
            "A constituição diz que A lei que instituir o plano plurianual estabelecerá, de forma regionalizada, as diretrizes, objetivos e metas da administração pública federal para as despesas de capital e outras delas decorrentes e para as relativas aos programas de duração continuada."
            "Programa finalístico é o conjunto coordenado de ações governamentais financiadas por recursos orçamentários e não orçamentários com vistas à concretização do objetivo;"

            "O documento é um espelho de todos os programas que compõe o planejamento plurianual do governo federal"
            "Programa, Órgão, Tipo de Programa, Objetivos Estratégicos, Público Alvo:, Problema, Causa do problema, Evidências do problema, Justificativa para a intervenção"
            "Evolução histórica, Comparações Internacionais, Relação com os ODS, Agentes Envolvidos, Articulação federativa, Marco Legal, Planos nacionais, setoriais e regionais"
            "Objetivo Geral, Objetivos específicos e Entregas"         

            "Objetivo é mudança na realidade social que o programa visa promover ao enfrentar o problema público;"
            " - **Objetivos Estratégicos** são  objetivos estratégicos - declarações objetivas e concisas que indicam as mudanças estratégicas a serem realizadas na sociedade no período compreendido pelo PPA 2024-2027; "
            " - **Objetivos Específicos** são os passos mais detalhados, concretos e mensuráveis para alcançar os objetivos estratégicos. "            
            "público-alvo é a população que deverá ser atendida e priorizada;"
            "órgão responsável é o órgão ou entidade federal responsável pelo alcance do objetivo do programa, do objetivo específico ou da entrega;"
            "objetivos específicos são o detalhamento do objetivo do programa que declara cada resultado esperado decorrente da entrega de bens e serviços ou de medidas institucionais e normativas, consideradas as limitações temporal e fiscal do PPA 2024-2027;"
            "Os objetivos específicos são caracterizados como 'Objetivo Específico: xxxx - Nome do objetivo'"
            "indicador é o instrumento que permite mensurar objetivamente o alcance da meta declarada;"
            "meta é o valor esperado para o indicador no período a que se refere;"
            "regionalização da meta é a distribuição das metas estipuladas para o programa no território;"
            "desagregação da meta por público é a definição de metas por públicos específicos;"
            "Os indicadores são identificados como 'Indicador: XXXX - Nome do indicador"
            "agenda transversal é o conjunto de atributos que encaminha problemas complexos de políticas públicas, podendo contemplar aquelas focalizadas em públicos-alvo ou temas específicos, que necessitam de uma abordagem multidimensional e integrada por parte do Estado para serem encaminhados de maneira eficaz e efetiva;"
            "valor global do programa é a estimativa dos recursos orçamentários e não-orçamentários, sendo os orçamentários segregados nas esferas fiscal, da seguridade social e de investimento, e os não-orçamentários divididos em subsídios tributários e creditícios, créditos de instituições financeiras públicas e outras fontes de financiamento;"
            "entrega é o atributo infralegal do PPA 2024-2027 que declara produtos (bens ou serviços) relevantes que contribuem para o alcance de objetivo específico do programa;"
            "medida institucional e normativa é o atributo infralegal do PPA 2024-2027 que declara atividades institucionais e normativas de caráter regulatório, de melhoria do ambiente de negócios ou de gestão relevantes para o alcance de objetivos específicos ou do programa;"
            "Todas as páginas possuem cabeçalho 'Ministério do Planejamento e Orçamento Mapeamento de Programas Integrantes do Plano Plurianual 2024-2027 Secretaria Nacional de Planejamento' Que deve ser ignorado."
            "Os programas e objetivos específicos possuem medidas institucionais e normativas voltadas a fazer com que o programa funcione. Então pode ser útil em perguntas sobre medidas tomadas para resolver o problema."  
            "São prioridades da administração pública federal, incluídas aquelas advindas do processo de participação social na elaboração do PPA 2024-2027: I - combate à fome e redução das desigualdades;  II - educação básica;  III - saúde: atenção primária e atenção especializada;  IV - Programa de Aceleração do Crescimento - Novo PAC;  V - neoindustrialização, trabalho, emprego e renda; e VI - combate ao desmatamento e enfrentamento da emergência climática."
            "São agendas transversais do PPA 2024-2027:  I - crianças e adolescentes;  II - mulheres;  III - igualdade racial;  IV - povos indígenas; e V - meio ambiente."            


        )
        ,
        model="gpt-4o",
        tools=[{"type": "file_search"}],
        tool_resources={
            "file_search": {
                "vector_store_ids": [vector_store.id]
            }
        }
    )

    # 8. Salva a configuração com os hashes
    with open(ASSISTANT_CONFIG_PATH, "w") as f:
        json.dump({
            "assistant_id": assistant.id,
            "file_ids": [f.id for f in uploaded_files],
            "vector_store_id": vector_store.id,
            "pdf_hashes": pdf_hashes
        }, f)

    end_time = time.time()
    if DEBUG_MODE:
        st.success(f"⏱️ Tempo total de configuração: {end_time - start_time:.2f} segundos")

    return assistant.id, [f.id for f in uploaded_files], vector_store.id        


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

# Botão para resetar tudo
if st.button("🔁 Recriar assistente e apagar histórico"):
    reset_config()

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
