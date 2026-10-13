from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import sqlite3
from datetime import date, datetime, timedelta, timezone
import os
import shutil
from pathlib import Path
import bcrypt
import jwt
from dotenv import load_dotenv
from storage import salvar_arquivo, remover_arquivo, obter_url_arquivo

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "banco.db"
UPLOADS_DIR = BASE_DIR / "uploads"

SECRET_KEY = os.getenv("SECRET_KEY", "chave_secreta_padrao_dev_casa_de_lio")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Rota segura para visualização/download de atestados (com suporte a Redirecionamento S3/MinIO ou Local)
@app.get("/uploads/{nome_arquivo:path}")
async def visualizar_atestado(request: Request, nome_arquivo: str):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
        
    url = obter_url_arquivo(nome_arquivo)
    if url.startswith("http://") or url.startswith("https://"):
        return RedirectResponse(url=url, status_code=307)
        
    caminho = UPLOADS_DIR / nome_arquivo
    if caminho.exists():
        return FileResponse(str(caminho))
    return Response("Arquivo não encontrado", status_code=404)

def pegar_conexao():
    conexao = sqlite3.connect(str(DB_PATH))
    conexao.row_factory = sqlite3.Row
    return conexao

def migrar_banco_se_necessario():
    conexao = pegar_conexao()
    cursor = conexao.cursor()
    # 1. Tabela de logs de auditoria (Conformidade LGPD)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            acao TEXT NOT NULL,
            alvo TEXT,
            data_hora TEXT NOT NULL
        )
    ''')
    # 2. Adicionar colunas de consentimento LGPD na tabela alunos se ainda não existirem
    cursor.execute("PRAGMA table_info(alunos)")
    colunas = [col['name'] for col in cursor.fetchall()]
    if 'consentimento_lgpd' not in colunas:
        cursor.execute("ALTER TABLE alunos ADD COLUMN consentimento_lgpd INTEGER DEFAULT 0")
    if 'data_consentimento' not in colunas:
        cursor.execute("ALTER TABLE alunos ADD COLUMN data_consentimento TEXT")

    # 3. Tabelas de Turmas / Cursos e Vínculo com Professores
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS turmas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL,
            descricao TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS professor_turmas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            turma_nome TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE,
            UNIQUE(usuario_id, turma_nome)
        )
    ''')

    # Popular turmas padrão e turmas existentes em alunos
    for nome_t, desc_t in [('Manhã', 'Turno Matutino'), ('Tarde', 'Turno Vespertino'), ('Noite', 'Turno Noturno')]:
        cursor.execute("INSERT OR IGNORE INTO turmas (nome, descricao) VALUES (?, ?)", (nome_t, desc_t))

    turmas_existentes = cursor.execute(
        'SELECT DISTINCT turma FROM alunos WHERE turma IS NOT NULL AND turma != ""'
    ).fetchall()
    for row in turmas_existentes:
        cursor.execute("INSERT OR IGNORE INTO turmas (nome, descricao) VALUES (?, ?)", (row['turma'], 'Turma Cadastrada'))
        
    conexao.commit()
    conexao.close()

# Executa migração na inicialização
migrar_banco_se_necessario()

def obter_todas_turmas(conexao) -> list[str]:
    turmas_db = conexao.execute('''
        SELECT nome FROM turmas
        UNION
        SELECT DISTINCT turma as nome FROM alunos WHERE turma IS NOT NULL AND turma != ""
        ORDER BY nome ASC
    ''').fetchall()
    return [t['nome'] for t in turmas_db]

def obter_turmas_permitidas(conexao, usuario: dict) -> list[str]:
    if not usuario:
        return []
    if usuario.get('perfil') == 'coordenacao':
        return obter_todas_turmas(conexao)
    
    # Para professor, retorna exclusivamente as turmas vinculadas a ele
    rows = conexao.execute('''
        SELECT pt.turma_nome
        FROM professor_turmas pt
        JOIN usuarios u ON pt.usuario_id = u.id
        WHERE u.login = ?
        ORDER BY pt.turma_nome ASC
    ''', (usuario.get('login', ''),)).fetchall()
    return [r['turma_nome'] for r in rows]

def registrar_auditoria(usuario: str, acao: str, alvo: str = None):
    try:
        conexao = pegar_conexao()
        conexao.execute(
            "INSERT INTO logs_auditoria (usuario, acao, alvo, data_hora) VALUES (?, ?, ?, ?)",
            (usuario, acao, alvo, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conexao.commit()
        conexao.close()
    except Exception as e:
        print(f"[LGPD Log Erro]: {e}")

# --- UTILITÁRIOS DE CRIPTOGRAFIA & JWT ---
def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode('utf-8'), senha_hash.encode('utf-8'))
    except Exception:
        # Fallback de compatibilidade caso haja senhas legadas em texto puro
        return senha == senha_hash

def criar_token_jwt(dados: dict) -> str:
    expiracao = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = dados.copy()
    payload.update({"exp": expiracao})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decodificar_token_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None

def obter_usuario_atual(request: Request) -> dict | None:
    token = None
    # 1. Verifica header Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    # 2. Verifica cookie seguro 'access_token'
    if not token:
        cookie_val = request.cookies.get("access_token")
        if cookie_val:
            cookie_val = cookie_val.strip('"')
            if cookie_val.startswith("Bearer "):
                token = cookie_val[7:]
            else:
                token = cookie_val
            
    if token:
        payload = decodificar_token_jwt(token)
        if payload and "sub" in payload:
            return {"login": payload["sub"], "perfil": payload.get("perfil", "professor")}
            
    # 3. Fallback de sessão
    if request.session.get("perfil"):
        return {"login": request.session.get("login", "usuario"), "perfil": request.session.get("perfil")}
        
    return None

# --- SISTEMA DE LOGIN COM JWT ---
@app.get("/login", response_class=HTMLResponse)
async def tela_login(request: Request):
    usuario = obter_usuario_atual(request)
    if usuario:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html")

@app.post("/login")
async def fazer_login(request: Request, login: str = Form(...), senha: str = Form(...)):
    conexao = pegar_conexao()
    usuario = conexao.execute('SELECT * FROM usuarios WHERE login = ?', (login,)).fetchone()
    
    if usuario and verificar_senha(senha, usuario['senha']):
        # Se a senha estava em texto puro (legada), atualiza silenciosamente para bcrypt
        if usuario['senha'] == senha:
            novo_hash = hash_senha(senha)
            conexao.execute('UPDATE usuarios SET senha = ? WHERE id = ?', (novo_hash, usuario['id']))
            conexao.commit()
            
        conexao.close()
        
        # Gera o token JWT
        token = criar_token_jwt({"sub": usuario['login'], "perfil": usuario['perfil']})
        
        # Sincroniza sessão para compatibilidade retroativa com templates Jinja
        request.session['perfil'] = usuario['perfil']
        request.session['login'] = usuario['login']
        
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            samesite="lax"
        )
        return response
        
    conexao.close()
    return RedirectResponse(url="/login?erro=1", status_code=303)
# --- NOVO: DASHBOARD ANALÍTICO (Apenas Coordenação) ---

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    
    total_alunos = conexao.execute('SELECT COUNT(*) as qtd FROM alunos').fetchone()['qtd']
    
    totais = conexao.execute('SELECT status, COUNT(*) as quantidade FROM presencas GROUP BY status').fetchall()
    dados_grafico = {"Presente": 0, "Falta": 0, "Falta justificada": 0}
    for linha in totais:
        dados_grafico[linha['status']] = linha['quantidade']
        
    total_registos = sum(dados_grafico.values())
    taxa_assiduidade = round((dados_grafico['Presente'] / total_registos * 100), 1) if total_registos > 0 else 0
    
    historico = conexao.execute('''
        SELECT data,
               SUM(CASE WHEN status = 'Presente' THEN 1 ELSE 0 END) as presentes,
               SUM(CASE WHEN status = 'Falta' THEN 1 ELSE 0 END) as faltas,
               SUM(CASE WHEN status = 'Falta justificada' THEN 1 ELSE 0 END) as justificadas
        FROM presencas
        GROUP BY data
        ORDER BY data ASC
    ''').fetchall()
    
    turmas_db = conexao.execute('''
        SELECT alunos.turma,
               SUM(CASE WHEN presencas.status = 'Presente' THEN 1 ELSE 0 END) as presentes,
               SUM(CASE WHEN presencas.status = 'Falta' THEN 1 ELSE 0 END) as faltas
        FROM presencas
        JOIN alunos ON presencas.aluno_id = alunos.id
        GROUP BY alunos.turma
    ''').fetchall()
    
    dados_turmas = {
        "nomes": [t['turma'] for t in turmas_db],
        "presentes": [t['presentes'] for t in turmas_db],
        "faltas": [t['faltas'] for t in turmas_db]
    }
    
    # NOVO: Alerta de Faltas com captação de telefone para WhatsApp
    alunos_risco_db = conexao.execute('''
        SELECT alunos.nome, alunos.turma, alunos.telefone, COUNT(presencas.id) as total_faltas
        FROM presencas
        JOIN alunos ON presencas.aluno_id = alunos.id
        WHERE presencas.status = 'Falta'
        GROUP BY alunos.id
        ORDER BY total_faltas DESC
        LIMIT 5
    ''').fetchall()
    
    # Prepara os números de telefone removendo traços e espaços para o WhatsApp
    alunos_risco = []
    for aluno in alunos_risco_db:
        aluno_dict = dict(aluno)
        telefone_limpo = ''.join(filter(str.isdigit, aluno_dict['telefone']))
        if len(telefone_limpo) >= 10:
            telefone_limpo = "55" + telefone_limpo # Adiciona o DDI do Brasil automaticamente
        aluno_dict['telefone_whatsapp'] = telefone_limpo
        alunos_risco.append(aluno_dict)
    
    conexao.close()
    
    datas = [datetime.strptime(linha['data'], '%Y-%m-%d').strftime('%d/%m/%Y') for linha in historico]
    presentes = [linha['presentes'] for linha in historico]
    faltas = [linha['faltas'] for linha in historico]
    justificadas = [linha['justificadas'] for linha in historico]
    
    return templates.TemplateResponse(
        request=request, 
        name="dashboard.html", 
        context={
            "total_alunos": total_alunos, "dados_grafico": dados_grafico,
            "taxa_assiduidade": taxa_assiduidade, "datas": datas, 
            "presentes": presentes, "faltas": faltas, "justificadas": justificadas,
            "dados_turmas": dados_turmas, "alunos_risco": alunos_risco
        }
    )
      
@app.get("/sair")
async def sair(request: Request):
    request.session.clear()
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="access_token")
    return response

# --- ROTAS PROTEGIDAS ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, data_chamada: str = None, turma: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
        
    data_atual = data_chamada if data_chamada else date.today().isoformat()
        
    conexao = pegar_conexao()
    turmas_disponiveis = obter_turmas_permitidas(conexao, usuario)

    # Se for professor e não possuir nenhuma turma vinculada
    if usuario['perfil'] != 'coordenacao' and not turmas_disponiveis:
        conexao.close()
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "alunos": [],
                "data_atual": data_atual,
                "turmas_disponiveis": [],
                "turma_selecionada": "",
                "sem_turma_vinculada": True
            }
        )

    # Se o professor tiver apenas 1 turma vinculada e nenhuma foi selecionada, seleciona automaticamente
    if usuario['perfil'] != 'coordenacao' and not turma and len(turmas_disponiveis) == 1:
        turma = turmas_disponiveis[0]

    query = '''
        SELECT a.id, a.nome, a.turma, p.status, p.atestado,
               (SELECT COUNT(*) FROM presencas WHERE aluno_id = a.id AND status = 'Falta') as total_faltas
        FROM alunos a
        LEFT JOIN presencas p ON a.id = p.aluno_id AND p.data = ?
    '''
    parametros = [data_atual]
    if turma and turma in turmas_disponiveis:
        query += ' WHERE a.turma = ?'
        parametros.append(turma)
    elif usuario['perfil'] != 'coordenacao':
        # Restringe estritamente às turmas às quais o professor tem acesso
        placeholders = ','.join(['?'] * len(turmas_disponiveis))
        query += f' WHERE a.turma IN ({placeholders})'
        parametros.extend(turmas_disponiveis)
        turma = ""
        
    query += ' ORDER BY a.nome ASC'
    alunos = conexao.execute(query, parametros).fetchall()
    conexao.close()
    
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={
            "alunos": alunos, 
            "data_atual": data_atual,
            "turmas_disponiveis": turmas_disponiveis,
            "turma_selecionada": turma if (turma and turma in turmas_disponiveis) else "",
            "sem_turma_vinculada": False
        }
    )

# NOVO: Rota que recebe o arquivo PDF/Imagem e justifica a falta automaticamente
@app.post("/anexar_atestado/{aluno_id}/{data_chamada}")
async def anexar_atestado(request: Request, aluno_id: int, data_chamada: str, arquivo: UploadFile = File(...), turma: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    if usuario['perfil'] != 'coordenacao':
        aluno_check = conexao.execute('SELECT turma FROM alunos WHERE id = ?', (aluno_id,)).fetchone()
        turmas_perm = obter_turmas_permitidas(conexao, usuario)
        if not aluno_check or aluno_check['turma'] not in turmas_perm:
            conexao.close()
            return RedirectResponse(url="/", status_code=303)

    # Limpa o nome do arquivo e salva no armazenamento configurado (Local ou S3/MinIO)
    nome_limpo = os.path.basename(arquivo.filename).replace(' ', '_')
    nome_seguro = f"atestado_{aluno_id}_{data_chamada}_{nome_limpo}"
    
    salvar_arquivo(arquivo.file, nome_seguro, content_type=arquivo.content_type or "application/octet-stream")
        
    existe = conexao.execute('SELECT id FROM presencas WHERE aluno_id = ? AND data = ?', (aluno_id, data_chamada)).fetchone()
    
    # Automatiza a Justificativa marcando 'Falta justificada' e anexando o arquivo
    if existe:
        conexao.execute('UPDATE presencas SET status = ?, atestado = ? WHERE id = ?', ('Falta justificada', nome_seguro, existe['id']))
    else:
        conexao.execute('INSERT INTO presencas (aluno_id, data, status, atestado) VALUES (?, ?, ?, ?)', (aluno_id, data_chamada, 'Falta justificada', nome_seguro))
        
    conexao.commit()
    conexao.close()
    
    # Auditoria LGPD
    registrar_auditoria(usuario['login'], "ANEXO_ATESTADO", f"Aluno ID {aluno_id} na data {data_chamada} (Arquivo: {nome_seguro})")
    
    # Devolve a tela atualizada preservando a data e a turma
    url = f"/?data_chamada={data_chamada}"
    if turma:
        url += f"&turma={turma}"
    return RedirectResponse(url=url, status_code=303)

@app.get("/registrar/{aluno_id}/{data_chamada}/{status}")
async def registrar(request: Request, aluno_id: int, data_chamada: str, status: str, turma: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    if usuario['perfil'] != 'coordenacao':
        aluno_check = conexao.execute('SELECT turma FROM alunos WHERE id = ?', (aluno_id,)).fetchone()
        turmas_perm = obter_turmas_permitidas(conexao, usuario)
        if not aluno_check or aluno_check['turma'] not in turmas_perm:
            conexao.close()
            return RedirectResponse(url="/", status_code=303)

    # Verifica se o aluno já tem um registo nesse dia específico
    existe = conexao.execute('SELECT id FROM presencas WHERE aluno_id = ? AND data = ?', (aluno_id, data_chamada)).fetchone()
    
    if existe:
        # Se já existe, ATUALIZA (permite corrigir erros)
        conexao.execute('UPDATE presencas SET status = ? WHERE id = ?', (status, existe['id']))
    else:
        # Se não existe, CRIA um novo registo
        conexao.execute('INSERT INTO presencas (aluno_id, data, status) VALUES (?, ?, ?)', (aluno_id, data_chamada, status))
        
    conexao.commit()
    conexao.close()
    
    # Devolve a professora para a MESMA data e turma que estava a editar
    url = f"/?data_chamada={data_chamada}"
    if turma:
        url += f"&turma={turma}"
    return RedirectResponse(url=url, status_code=303)

@app.get("/exportar")
async def exportar(request: Request, mes: str = None, turma: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    turmas_perm = obter_turmas_permitidas(conexao, usuario)
    
    # Consulta base
    query = '''
        SELECT alunos.nome, alunos.turma, presencas.data, presencas.status 
        FROM presencas
        JOIN alunos ON presencas.aluno_id = alunos.id
        WHERE 1=1
    '''
    parametros = []
    
    # Se a pessoa escolheu um mês (O formato chega como YYYY-MM)
    if mes:
        query += " AND presencas.data LIKE ?"
        parametros.append(f"{mes}%")
        
    # Se a pessoa escolheu uma turma específica
    if turma:
        query += " AND alunos.turma = ?"
        parametros.append(turma)
    elif usuario['perfil'] != 'coordenacao':
        if not turmas_perm:
            conexao.close()
            return Response(content="Nome;Turma;Data;Status\n".encode('utf-8-sig'), media_type="text/csv; charset=utf-8-sig")
        placeholders = ','.join(['?'] * len(turmas_perm))
        query += f" AND alunos.turma IN ({placeholders})"
        parametros.extend(turmas_perm)
        
    query += " ORDER BY presencas.data DESC, alunos.nome ASC"
    
    relatorio = conexao.execute(query, parametros).fetchall()
    conexao.close()
    
    texto_csv = "Nome;Turma;Data;Status\n"
    for linha in relatorio:
        data_br = datetime.strptime(linha['data'], '%Y-%m-%d').strftime('%d/%m/%Y')
        texto_csv += f"{linha['nome']};{linha['turma']};{data_br};{linha['status']}\n"
        
    return Response(
        content=texto_csv.encode('utf-8-sig'),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=relatorio_presencas.csv"}
    )
    
@app.get("/cadastrar", response_class=HTMLResponse)
async def tela_cadastrar(request: Request):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
    conexao = pegar_conexao()
    turmas_disponiveis = obter_todas_turmas(conexao)
    conexao.close()
    return templates.TemplateResponse(request=request, name="cadastro.html", context={"turmas_disponiveis": turmas_disponiveis})

# NOVO: A rota invisível que recebe os dados do formulário e salva no banco
@app.post("/cadastrar")
async def salvar_cadastro(request: Request):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    
    nome = formulario.get('nome')
    data_nascimento = formulario.get('data_nascimento')
    turma = formulario.get('turma')
    telefone = formulario.get('telefone')
    cep = formulario.get('cep', '')
    rua = formulario.get('rua', '')
    numero = formulario.get('numero', '')
    bairro = formulario.get('bairro', '')
    cidade = formulario.get('cidade', '')
    referencia = formulario.get('referencia', '')
    contato_emergencia = formulario.get('contato_emergencia', '')
    telefone_emergencia = formulario.get('telefone_emergencia', '')
    condicao_medica = formulario.get('condicao_medica', '')
    nome_responsavel = formulario.get('nome_responsavel', '')
    autorizacao = 1 if formulario.get('autorizacao') == 'on' else 0
    consentimento_lgpd = 1 if formulario.get('consentimento_lgpd') == 'on' else 0
    data_consentimento = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if consentimento_lgpd else None
    
    conexao = pegar_conexao()
    cursor = conexao.cursor()
    if turma:
        cursor.execute("INSERT OR IGNORE INTO turmas (nome, descricao) VALUES (?, ?)", (turma, "Turma Cadastrada"))

    cursor.execute('''
        INSERT INTO alunos (
            nome, data_nascimento, turma, telefone, 
            cep, rua, numero, bairro, cidade, referencia,
            contato_emergencia, telefone_emergencia, condicao_medica,
            nome_responsavel, autorizacao_pais, consentimento_lgpd, data_consentimento
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (nome, data_nascimento, turma, telefone, cep, rua, numero, bairro, cidade, referencia, 
          contato_emergencia, telefone_emergencia, condicao_medica, nome_responsavel, autorizacao,
          consentimento_lgpd, data_consentimento))
    
    aluno_id = cursor.lastrowid # Capta o ID exato do aluno que acabou de ser gravado
    conexao.commit()
    conexao.close()
    
    # Registro de auditoria LGPD
    registrar_auditoria(usuario['login'], "CADASTRO_ALUNO", f"Matrícula: {nome} (ID: {aluno_id}, Turma: {turma})")
    
    # NOVO: Em vez do Dashboard, atira para a ficha de impressão
    return RedirectResponse(url=f"/ficha/{aluno_id}", status_code=303)

# NOVO: Rota exclusiva para gerar a ficha em tamanho A4
@app.get("/ficha/{aluno_id}", response_class=HTMLResponse)
async def gerar_ficha(request: Request, aluno_id: int):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    aluno = conexao.execute('SELECT * FROM alunos WHERE id = ?', (aluno_id,)).fetchone()
    conexao.close()
    
    return templates.TemplateResponse(request=request, name="ficha.html", context={"aluno": aluno})

@app.get("/usuarios/novo", response_class=HTMLResponse)
async def tela_novo_usuario(request: Request, sucesso: str = None):
    # Proteção estrita: Apenas a coordenação pode aceder
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    turmas_disponiveis = obter_todas_turmas(conexao)
    usuarios_db = conexao.execute("SELECT id, login, perfil FROM usuarios ORDER BY perfil ASC, login ASC").fetchall()
    usuarios_sistema = [dict(u) for u in usuarios_db]

    professores_db = conexao.execute("SELECT id, login, perfil FROM usuarios WHERE perfil = 'professor' ORDER BY login ASC").fetchall()
    professores = []
    for prof in professores_db:
        turmas_prof = conexao.execute(
            "SELECT turma_nome FROM professor_turmas WHERE usuario_id = ? ORDER BY turma_nome ASC",
            (prof['id'],)
        ).fetchall()
        professores.append({
            "id": prof['id'],
            "login": prof['login'],
            "turmas": [t['turma_nome'] for t in turmas_prof]
        })
    conexao.close()
    return templates.TemplateResponse(
        request=request,
        name="novo_usuario.html",
        context={
            "turmas_disponiveis": turmas_disponiveis,
            "professores": professores,
            "usuarios_sistema": usuarios_sistema,
            "sucesso": sucesso
        }
    )

@app.post("/usuarios/alterar_senha")
async def alterar_senha_usuario(request: Request):
    # Proteção estrita: Apenas a coordenação pode alterar senhas de usuários
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)

    formulario = await request.form()
    usuario_id = formulario.get('usuario_id')
    nova_senha = (formulario.get('nova_senha') or '').strip()

    if usuario_id and nova_senha:
        conexao = pegar_conexao()
        alvo = conexao.execute("SELECT id, login, perfil FROM usuarios WHERE id = ?", (int(usuario_id),)).fetchone()
        if alvo:
            senha_hasheada = hash_senha(nova_senha)
            conexao.execute("UPDATE usuarios SET senha = ? WHERE id = ?", (senha_hasheada, int(usuario_id)))
            conexao.commit()
            registrar_auditoria(
                usuario['login'],
                "ALTERACAO_SENHA",
                f"Senha redefinida para o usuário: {alvo['login']} (Perfil: {alvo['perfil']})"
            )
        conexao.close()
        return RedirectResponse(url="/usuarios/novo?sucesso=senha", status_code=303)

    return RedirectResponse(url="/usuarios/novo", status_code=303)

@app.post("/usuarios/novo")
async def salvar_usuario(request: Request):
    # Proteção estrita na gravação dos dados
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    login = formulario.get('login')
    senha = formulario.get('senha')
    perfil = formulario.get('perfil')
    if hasattr(formulario, 'getlist'):
        turmas_selecionadas = formulario.getlist('turmas')
    else:
        val_t = formulario.get('turmas', [])
        turmas_selecionadas = val_t if isinstance(val_t, list) else ([val_t] if val_t else [])
    
    senha_hasheada = hash_senha(senha)
    
    conexao = pegar_conexao()
    cursor = conexao.cursor()
    try:
        cursor.execute('''
            INSERT INTO usuarios (login, senha, perfil) 
            VALUES (?, ?, ?)
        ''', (login, senha_hasheada, perfil))
        novo_user_id = cursor.lastrowid
        if perfil == 'professor' and turmas_selecionadas:
            for nome_t in turmas_selecionadas:
                cursor.execute(
                    "INSERT OR IGNORE INTO professor_turmas (usuario_id, turma_nome) VALUES (?, ?)",
                    (novo_user_id, nome_t)
                )
        conexao.commit()
        detalhe_turmas = f", Turmas: {', '.join(turmas_selecionadas)}" if (perfil == 'professor' and turmas_selecionadas) else ""
        registrar_auditoria(usuario['login'], "CRIACAO_USUARIO", f"Novo usuário criado: {login} (Perfil: {perfil}{detalhe_turmas})")
    except Exception:
        # Se o utilizador já existir (o login é UNIQUE), o sistema ignora o erro para não quebrar
        pass 
    finally:
        conexao.close()
        
    # Após criar a conta, devolve a coordenação ao Painel Analítico
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/alunos", response_class=HTMLResponse)
async def listar_alunos(request: Request):
    # Proteção: Apenas a coordenação pode ver o diretório completo
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    # Busca os alunos em ordem alfabética
    alunos_db = conexao.execute('''
        SELECT id, nome, turma, telefone, data_nascimento 
        FROM alunos 
        ORDER BY nome ASC
    ''').fetchall()
    conexao.close()
    
    return templates.TemplateResponse(
        request=request, 
        name="lista_alunos.html", 
        context={"alunos": alunos_db}
    )

# --- ROTAS DE GESTÃO DE ALUNOS (EDIÇÃO, EXCLUSÃO E HISTÓRICO) ---
@app.get("/alunos/editar/{aluno_id}", response_class=HTMLResponse)
async def tela_editar_aluno(request: Request, aluno_id: int):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    aluno = conexao.execute('SELECT * FROM alunos WHERE id = ?', (aluno_id,)).fetchone()
    turmas_disponiveis = obter_todas_turmas(conexao)
    conexao.close()
    
    if not aluno:
        return RedirectResponse(url="/alunos", status_code=303)
        
    return templates.TemplateResponse(
        request=request,
        name="editar_aluno.html",
        context={"aluno": aluno, "turmas_disponiveis": turmas_disponiveis}
    )

@app.post("/alunos/editar/{aluno_id}")
async def salvar_edicao_aluno(request: Request, aluno_id: int):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    nome = formulario.get('nome')
    data_nascimento = formulario.get('data_nascimento')
    turma = formulario.get('turma')
    telefone = formulario.get('telefone')
    cep = formulario.get('cep', '')
    rua = formulario.get('rua', '')
    numero = formulario.get('numero', '')
    bairro = formulario.get('bairro', '')
    cidade = formulario.get('cidade', '')
    referencia = formulario.get('referencia', '')
    contato_emergencia = formulario.get('contato_emergencia', '')
    telefone_emergencia = formulario.get('telefone_emergencia', '')
    condicao_medica = formulario.get('condicao_medica', '')
    nome_responsavel = formulario.get('nome_responsavel', '')
    autorizacao = 1 if formulario.get('autorizacao') == 'on' else 0
    consentimento_lgpd = 1 if formulario.get('consentimento_lgpd') == 'on' else 0
    data_consentimento = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if consentimento_lgpd else None
    
    conexao = pegar_conexao()
    if turma:
        conexao.execute("INSERT OR IGNORE INTO turmas (nome, descricao) VALUES (?, ?)", (turma, "Turma Cadastrada"))

    conexao.execute('''
        UPDATE alunos SET
            nome = ?, data_nascimento = ?, turma = ?, telefone = ?,
            cep = ?, rua = ?, numero = ?, bairro = ?, cidade = ?, referencia = ?,
            contato_emergencia = ?, telefone_emergencia = ?, condicao_medica = ?,
            nome_responsavel = ?, autorizacao_pais = ?,
            consentimento_lgpd = ?,
            data_consentimento = CASE WHEN ? = 1 AND (data_consentimento IS NULL OR data_consentimento = '') THEN ? ELSE data_consentimento END
        WHERE id = ?
    ''', (nome, data_nascimento, turma, telefone, cep, rua, numero, bairro, cidade, referencia,
          contato_emergencia, telefone_emergencia, condicao_medica, nome_responsavel, autorizacao,
          consentimento_lgpd, consentimento_lgpd, data_consentimento, aluno_id))
    conexao.commit()
    conexao.close()
    
    registrar_auditoria(usuario['login'], "EDICAO_ALUNO", f"Aluno atualizado: {nome} (ID: {aluno_id})")
    return RedirectResponse(url="/alunos", status_code=303)

@app.post("/alunos/excluir/{aluno_id}")
async def excluir_aluno(request: Request, aluno_id: int):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    aluno_info = conexao.execute('SELECT nome FROM alunos WHERE id = ?', (aluno_id,)).fetchone()
    nome_aluno = aluno_info['nome'] if aluno_info else f"ID {aluno_id}"

    # 1. Apaga os arquivos de atestados anexados ao aluno (se existirem, seja Local ou S3/MinIO)
    atestados = conexao.execute('SELECT atestado FROM presencas WHERE aluno_id = ? AND atestado IS NOT NULL', (aluno_id,)).fetchall()
    for row in atestados:
        if row['atestado']:
            remover_arquivo(row['atestado'])
                    
    # 2. Remove as presenças associadas
    conexao.execute('DELETE FROM presencas WHERE aluno_id = ?', (aluno_id,))
    # 3. Remove o registro do aluno (Eliminação definitiva do titular - Art. 18 LGPD)
    conexao.execute('DELETE FROM alunos WHERE id = ?', (aluno_id,))
    conexao.commit()
    conexao.close()
    
    registrar_auditoria(usuario['login'], "EXCLUSAO_ALUNO", f"Eliminação de dados do titular: {nome_aluno} (ID: {aluno_id})")
    return RedirectResponse(url="/alunos", status_code=303)

@app.get("/alunos/{aluno_id}/historico", response_class=HTMLResponse)
async def historico_aluno(request: Request, aluno_id: int):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    aluno = conexao.execute('SELECT * FROM alunos WHERE id = ?', (aluno_id,)).fetchone()
    if not aluno:
        conexao.close()
        return RedirectResponse(url="/", status_code=303)

    if usuario['perfil'] != 'coordenacao':
        turmas_perm = obter_turmas_permitidas(conexao, usuario)
        if aluno['turma'] not in turmas_perm:
            conexao.close()
            return RedirectResponse(url="/", status_code=303)
        
    registros_db = conexao.execute('''
        SELECT id, data, status, atestado
        FROM presencas
        WHERE aluno_id = ?
        ORDER BY data DESC
    ''', (aluno_id,)).fetchall()
    conexao.close()
    
    total_aulas = len(registros_db)
    presentes = sum(1 for r in registros_db if r['status'] == 'Presente')
    faltas = sum(1 for r in registros_db if r['status'] == 'Falta')
    justificadas = sum(1 for r in registros_db if r['status'] == 'Falta justificada')
    taxa_assiduidade = round((presentes / total_aulas * 100), 1) if total_aulas > 0 else 0
    
    historico = []
    for r in registros_db:
        reg_dict = dict(r)
        try:
            reg_dict['data_formatada'] = datetime.strptime(r['data'], '%Y-%m-%d').strftime('%d/%m/%Y')
        except Exception:
            reg_dict['data_formatada'] = r['data']
        historico.append(reg_dict)
        
    metricas = {
        "total_aulas": total_aulas,
        "presentes": presentes,
        "faltas": faltas,
        "justificadas": justificadas,
        "taxa_assiduidade": taxa_assiduidade
    }
    
    return templates.TemplateResponse(
        request=request,
        name="historico_aluno.html",
        context={"aluno": aluno, "historico": historico, "metricas": metricas}
    )

# --- ROTAS DE TURMAS / CURSOS E VÍNCULO DE PROFESSORES ---
@app.get("/turmas", response_class=HTMLResponse)
async def tela_turmas(request: Request, sucesso: str = None, erro: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    turmas_permitidas = obter_turmas_permitidas(conexao, usuario)
    
    # Busca dados das turmas cadastradas
    todas_turmas_db = conexao.execute('''
        SELECT t.id, t.nome, t.descricao,
               (SELECT COUNT(*) FROM alunos a WHERE a.turma = t.nome) as total_alunos
        FROM turmas t
        ORDER BY t.nome ASC
    ''').fetchall()
    
    lista_turmas = []
    for t in todas_turmas_db:
        if usuario['perfil'] != 'coordenacao' and t['nome'] not in turmas_permitidas:
            continue
        profs_t = conexao.execute('''
            SELECT u.id, u.login
            FROM professor_turmas pt
            JOIN usuarios u ON pt.usuario_id = u.id
            WHERE pt.turma_nome = ?
            ORDER BY u.login ASC
        ''', (t['nome'],)).fetchall()
        lista_turmas.append({
            "id": t['id'],
            "nome": t['nome'],
            "descricao": t['descricao'] or "Curso / Turma Ativa",
            "total_alunos": t['total_alunos'],
            "professores": [p['login'] for p in profs_t],
            "professores_ids": [p['id'] for p in profs_t]
        })
        
    professores_sistema = []
    if usuario['perfil'] == 'coordenacao':
        profs_db = conexao.execute("SELECT id, login FROM usuarios WHERE perfil = 'professor' ORDER BY login ASC").fetchall()
        for p in profs_db:
            pt_rows = conexao.execute("SELECT turma_nome FROM professor_turmas WHERE usuario_id = ? ORDER BY turma_nome ASC", (p['id'],)).fetchall()
            professores_sistema.append({
                "id": p['id'],
                "login": p['login'],
                "turmas": [r['turma_nome'] for r in pt_rows]
            })
            
    conexao.close()
    return templates.TemplateResponse(
        request=request,
        name="turmas.html",
        context={
            "turmas": lista_turmas,
            "professores": professores_sistema,
            "usuario_atual": usuario,
            "sucesso": sucesso,
            "erro": erro
        }
    )

@app.post("/turmas/nova")
async def criar_turma(request: Request):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    nome = (formulario.get('nome') or '').strip()
    descricao = (formulario.get('descricao') or '').strip()
    if hasattr(formulario, 'getlist'):
        professores_ids = formulario.getlist('professores')
    else:
        val_p = formulario.get('professores', [])
        professores_ids = val_p if isinstance(val_p, list) else ([val_p] if val_p else [])
        
    if nome:
        conexao = pegar_conexao()
        cursor = conexao.cursor()
        cursor.execute("INSERT OR IGNORE INTO turmas (nome, descricao) VALUES (?, ?)", (nome, descricao))
        for pid in professores_ids:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO professor_turmas (usuario_id, turma_nome) VALUES (?, ?)",
                    (int(pid), nome)
                )
            except Exception:
                pass
        conexao.commit()
        conexao.close()
        registrar_auditoria(usuario['login'], "CRIACAO_TURMA", f"Nova turma/curso criada: {nome}")
        
    return RedirectResponse(url="/turmas", status_code=303)

@app.post("/turmas/vincular")
async def vincular_professor_turmas(request: Request):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    usuario_id = formulario.get('usuario_id')
    origem = formulario.get('origem', 'turmas')
    if hasattr(formulario, 'getlist'):
        turmas_selecionadas = formulario.getlist('turmas')
    else:
        val_t = formulario.get('turmas', [])
        turmas_selecionadas = val_t if isinstance(val_t, list) else ([val_t] if val_t else [])
        
    if usuario_id:
        conexao = pegar_conexao()
        prof_row = conexao.execute("SELECT login FROM usuarios WHERE id = ?", (int(usuario_id),)).fetchone()
        nome_prof = prof_row['login'] if prof_row else f"ID {usuario_id}"
        
        conexao.execute("DELETE FROM professor_turmas WHERE usuario_id = ?", (int(usuario_id),))
        for nome_t in turmas_selecionadas:
            conexao.execute(
                "INSERT OR IGNORE INTO professor_turmas (usuario_id, turma_nome) VALUES (?, ?)",
                (int(usuario_id), nome_t)
            )
        conexao.commit()
        conexao.close()
        registrar_auditoria(
            usuario['login'],
            "VINCULO_PROFESSOR_TURMA",
            f"Professor {nome_prof} vinculado às turmas: {', '.join(turmas_selecionadas) if turmas_selecionadas else 'Nenhuma'}"
        )
        
    if origem == 'usuarios':
        return RedirectResponse(url="/usuarios/novo", status_code=303)
    return RedirectResponse(url="/turmas", status_code=303)

@app.post("/turmas/editar/{turma_id}")
async def editar_turma(request: Request, turma_id: int):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    novo_nome = (formulario.get('nome') or '').strip()
    nova_descricao = (formulario.get('descricao') or '').strip()
    if hasattr(formulario, 'getlist'):
        professores_ids = formulario.getlist('professores')
    else:
        val_p = formulario.get('professores', [])
        professores_ids = val_p if isinstance(val_p, list) else ([val_p] if val_p else [])
        
    if not novo_nome:
        return RedirectResponse(url="/turmas", status_code=303)
        
    conexao = pegar_conexao()
    cursor = conexao.cursor()
    turma_atual = cursor.execute("SELECT id, nome, descricao FROM turmas WHERE id = ?", (turma_id,)).fetchone()
    if not turma_atual:
        conexao.close()
        return RedirectResponse(url="/turmas", status_code=303)
        
    nome_antigo = turma_atual['nome']
    
    # Se o nome mudou, verifica se já não existe outra turma com o novo nome
    if novo_nome != nome_antigo:
        conflito = cursor.execute("SELECT id FROM turmas WHERE nome = ? AND id != ?", (novo_nome, turma_id)).fetchone()
        if conflito:
            conexao.close()
            return RedirectResponse(url="/turmas?erro=nome_duplicado", status_code=303)
            
    try:
        # 1. Atualiza registro na tabela turmas
        cursor.execute("UPDATE turmas SET nome = ?, descricao = ? WHERE id = ?", (novo_nome, nova_descricao, turma_id))
        
        # 2. Se o nome mudou, cascateia a alteração em alunos e professor_turmas
        if novo_nome != nome_antigo:
            cursor.execute("UPDATE alunos SET turma = ? WHERE turma = ?", (novo_nome, nome_antigo))
            cursor.execute("UPDATE professor_turmas SET turma_nome = ? WHERE turma_nome = ?", (novo_nome, nome_antigo))
            
        # 3. Atualiza os vínculos de professores desta turma
        cursor.execute("DELETE FROM professor_turmas WHERE turma_nome = ?", (novo_nome,))
        for pid in professores_ids:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO professor_turmas (usuario_id, turma_nome) VALUES (?, ?)",
                    (int(pid), novo_nome)
                )
            except Exception:
                pass
                
        conexao.commit()
        registrar_auditoria(
            usuario['login'],
            "EDICAO_TURMA",
            f"Turma atualizada (ID {turma_id}): '{nome_antigo}' -> '{novo_nome}'"
        )
    except sqlite3.IntegrityError:
        conexao.rollback()
        conexao.close()
        return RedirectResponse(url="/turmas?erro=nome_duplicado", status_code=303)
    finally:
        conexao.close()
        
    return RedirectResponse(url="/turmas?sucesso=editada", status_code=303)

@app.post("/turmas/excluir/{turma_id}")
async def excluir_turma(request: Request, turma_id: int):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    turma_row = conexao.execute("SELECT nome FROM turmas WHERE id = ?", (turma_id,)).fetchone()
    if turma_row:
        nome_t = turma_row['nome']
        qtd_alunos = conexao.execute("SELECT COUNT(*) as c FROM alunos WHERE turma = ?", (nome_t,)).fetchone()['c']
        if qtd_alunos == 0:
            conexao.execute("DELETE FROM professor_turmas WHERE turma_nome = ?", (nome_t,))
            conexao.execute("DELETE FROM turmas WHERE id = ?", (turma_id,))
            conexao.commit()
            registrar_auditoria(usuario['login'], "EXCLUSAO_TURMA", f"Turma excluída: {nome_t}")
    conexao.close()
    return RedirectResponse(url="/turmas", status_code=303)

# --- ROTA DE AUDITORIA E CONFORMIDADE LGPD ---
@app.get("/auditoria", response_class=HTMLResponse)
async def tela_auditoria(request: Request):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    logs_db = conexao.execute('''
        SELECT id, usuario, acao, alvo, data_hora 
        FROM logs_auditoria 
        ORDER BY id DESC 
        LIMIT 100
    ''').fetchall()
    conexao.close()
    
    logs = []
    for log in logs_db:
        log_dict = dict(log)
        try:
            dt = datetime.strptime(log['data_hora'], "%Y-%m-%d %H:%M:%S")
            log_dict['data_hora_formatada'] = dt.strftime("%d/%m/%Y às %H:%M:%S")
        except Exception:
            log_dict['data_hora_formatada'] = log['data_hora']
        logs.append(log_dict)
        
    return templates.TemplateResponse(
        request=request, 
        name="auditoria.html", 
        context={"logs": logs, "usuario_atual": usuario}
    )