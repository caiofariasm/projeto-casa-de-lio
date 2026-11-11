from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import sqlite3
from datetime import date, datetime, timedelta, timezone
import os
import logging
import secrets
import uuid
import re
import csv
import io
from urllib.parse import urlencode
from pathlib import Path
import bcrypt
import jwt
from dotenv import load_dotenv
from storage import salvar_arquivo, remover_arquivo, obter_conteudo_arquivo
from catalogo_turmas import TURMAS_INICIAIS

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "banco.db"
UPLOADS_DIR = BASE_DIR / "uploads"

logger = logging.getLogger(__name__)
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)
    logger.warning(
        "SECRET_KEY não configurada; usando uma chave temporária. "
        "Sessões deixarão de valer após reinícios e podem falhar entre instâncias."
    )
elif len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY configurada deve ter pelo menos 32 caracteres aleatórios.")
ALGORITHM = "HS256"
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
ALLOW_WEAK_DEV_PASSWORDS = ENVIRONMENT == "development"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI()
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=COOKIE_SECURE, same_site="lax")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.middleware("http")
async def validar_origem_requisicoes_mutaveis(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origem = request.headers.get("origin") or request.headers.get("referer")
        if not origem:
            return Response("Cabeçalho de origem obrigatório", status_code=403)
        from urllib.parse import urlsplit
        if urlsplit(origem).netloc != request.headers.get("host"):
            return Response("Origem não permitida", status_code=403)
    return await call_next(request)

# Rota segura para visualização/download de atestados (com suporte a Redirecionamento S3/MinIO ou Local)
@app.get("/uploads/{nome_arquivo:path}")
async def visualizar_atestado(request: Request, nome_arquivo: str):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
        
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", nome_arquivo) or ".." in nome_arquivo:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    if os.getenv("STORAGE_TYPE", "local").strip().lower() == "s3":
        conteudo = obter_conteudo_arquivo(nome_arquivo)
        if conteudo is None:
            return Response("Arquivo não encontrado", status_code=404)
        return Response(conteudo, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"', "X-Content-Type-Options": "nosniff"})
        
    caminho = (UPLOADS_DIR / nome_arquivo).resolve()
    if caminho.is_relative_to(UPLOADS_DIR.resolve()) and caminho.is_file():
        return FileResponse(str(caminho), headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"', "X-Content-Type-Options": "nosniff"})
    return Response("Arquivo não encontrado", status_code=404)

def _obter_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


class _RowCompat(dict):
    """Compatível tanto com row['coluna'] quanto com row[0] e dict(row), igual ao sqlite3.Row."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _PostgresCursorWrapper:
    def __init__(self, raw_cursor):
        self._cur = raw_cursor
        self.lastrowid = None

    def _traduzir_sql(self, sql: str) -> tuple[str, bool]:
        s = sql.strip()
        if s.upper() == "BEGIN IMMEDIATE":
            return "BEGIN", False
        # Converte INSERT OR IGNORE do SQLite para ON CONFLICT DO NOTHING do Postgres
        if "INSERT OR IGNORE INTO professor_turmas" in s:
            s = s.replace("INSERT OR IGNORE INTO", "INSERT INTO").rstrip(";") + " ON CONFLICT (usuario_id, turma_nome) DO NOTHING"
        elif "INSERT OR IGNORE INTO turmas" in s:
            s = s.replace("INSERT OR IGNORE INTO", "INSERT INTO").rstrip(";") + " ON CONFLICT (nome) DO NOTHING"
        s = s.replace("COLLATE NOCASE", "")
        s = s.replace("?", "%s")
        upper = s.upper()
        precisa_returning = (
            upper.startswith("INSERT INTO ALUNOS")
            or upper.startswith("INSERT INTO USUARIOS")
            or upper.startswith("INSERT INTO TURMAS")
        ) and "ON CONFLICT" not in upper and "RETURNING" not in upper
        if precisa_returning:
            s = s.rstrip(";") + " RETURNING id"
        return s, precisa_returning

    def execute(self, sql: str, params=()):
        sql_pg, precisa_returning = self._traduzir_sql(sql)
        try:
            self._cur.execute(sql_pg, tuple(params) if params else None)
        except Exception as e:
            if e.__class__.__name__ in ("IntegrityError", "UniqueViolation", "ForeignKeyViolation", "CheckViolation"):
                raise sqlite3.IntegrityError(str(e)) from e
            raise
        if precisa_returning:
            row = self._cur.fetchone()
            self.lastrowid = row[0] if row else None
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._cur.description]
        return _RowCompat(zip(cols, row))

    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self._cur.description]
        return [_RowCompat(zip(cols, r)) for r in rows]


class _PostgresConnWrapper:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return _PostgresCursorWrapper(self._conn.cursor())

    def execute(self, sql: str, params=()):
        cur = self.cursor()
        return cur.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def pegar_conexao():
    db_url = _obter_database_url()
    if db_url:
        import psycopg2
        raw_conn = psycopg2.connect(db_url)
        return _PostgresConnWrapper(raw_conn)
    conexao = sqlite3.connect(str(DB_PATH))
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA busy_timeout = 5000")
    return conexao


def migrar_banco_se_necessario():
    db_url = _obter_database_url()
    conexao = pegar_conexao()
    cursor = conexao.cursor()

    if db_url:
        # Esquema completo para PostgreSQL (Neon / Supabase / Render)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alunos (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                turma TEXT NOT NULL,
                data_nascimento TEXT,
                telefone TEXT,
                cep TEXT,
                rua TEXT,
                numero TEXT,
                bairro TEXT,
                cidade TEXT,
                referencia TEXT,
                contato_emergencia TEXT,
                telefone_emergencia TEXT,
                condicao_medica TEXT,
                nome_responsavel TEXT,
                autorizacao_pais INTEGER,
                consentimento_lgpd INTEGER DEFAULT 0,
                data_consentimento TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS presencas (
                id SERIAL PRIMARY KEY,
                aluno_id INTEGER REFERENCES alunos(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                status TEXT NOT NULL,
                atestado TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                login TEXT UNIQUE NOT NULL,
                senha TEXT NOT NULL,
                perfil TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs_auditoria (
                id SERIAL PRIMARY KEY,
                usuario TEXT NOT NULL,
                acao TEXT NOT NULL,
                alvo TEXT,
                data_hora TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS turmas (
                id SERIAL PRIMARY KEY,
                nome TEXT UNIQUE NOT NULL,
                descricao TEXT,
                limite_alunos INTEGER CHECK (limite_alunos IS NULL OR limite_alunos > 0)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS professor_turmas (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                turma_nome TEXT NOT NULL,
                UNIQUE(usuario_id, turma_nome)
            )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_presencas_aluno_data ON presencas(aluno_id, data)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alunos_turma ON alunos(turma)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_presencas_aluno_data ON presencas(aluno_id, data)")

        # Garante que o usuário coord exista no banco na nuvem
        coord_existe = cursor.execute("SELECT id FROM usuarios WHERE login = ?", ("coord",)).fetchone()
        if not coord_existe:
            senha_inicial = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "").strip() or "123"
            senha_hash = bcrypt.hashpw(senha_inicial.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            cursor.execute(
                "INSERT INTO usuarios (login, senha, perfil) VALUES (?, ?, ?)",
                ("coord", senha_hash, "coordenacao"),
            )
        conexao.commit()
        conexao.close()
        return

    # Migração para SQLite local
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alunos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            turma TEXT NOT NULL,
            data_nascimento TEXT,
            telefone TEXT,
            cep TEXT,
            rua TEXT,
            numero TEXT,
            bairro TEXT,
            cidade TEXT,
            referencia TEXT,
            contato_emergencia TEXT,
            telefone_emergencia TEXT,
            condicao_medica TEXT,
            nome_responsavel TEXT,
            autorizacao_pais INTEGER,
            consentimento_lgpd INTEGER DEFAULT 0,
            data_consentimento TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS presencas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aluno_id INTEGER,
            data TEXT NOT NULL,
            status TEXT NOT NULL,
            atestado TEXT,
            FOREIGN KEY (aluno_id) REFERENCES alunos (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            perfil TEXT NOT NULL
        )
    ''')
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
            descricao TEXT,
            limite_alunos INTEGER CHECK (limite_alunos IS NULL OR limite_alunos > 0)
        )
    ''')
    colunas_turmas = {col['name'] for col in cursor.execute("PRAGMA table_info(turmas)").fetchall()}
    if 'limite_alunos' not in colunas_turmas:
        cursor.execute("ALTER TABLE turmas ADD COLUMN limite_alunos INTEGER")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS professor_turmas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            turma_nome TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE,
            UNIQUE(usuario_id, turma_nome)
        )
    ''')

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_presencas_aluno_data ON presencas(aluno_id, data)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alunos_turma ON alunos(turma)")
    duplicatas = cursor.execute("SELECT 1 FROM presencas GROUP BY aluno_id, data HAVING COUNT(*) > 1 LIMIT 1").fetchone()
    if not duplicatas:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_presencas_aluno_data ON presencas(aluno_id, data)")
    else:
        logger.error("Banco contém presenças duplicadas; índice único não aplicado. Corrija os duplicados manualmente.")

    # Sincroniza eventuais turmas já vinculadas a alunos cadastrados
    turmas_existentes = cursor.execute(
        "SELECT DISTINCT turma FROM alunos WHERE turma IS NOT NULL AND turma != ''"
    ).fetchall()
    for row in turmas_existentes:
        cursor.execute("INSERT OR IGNORE INTO turmas (nome, descricao) VALUES (?, ?)", (row['turma'], 'Turma Cadastrada'))
        
    conexao.commit()
    conexao.close()

# Executa migração na inicialização
migrar_banco_se_necessario()


@app.get("/health")
async def health_check():
    """Rota leve para manter o servidor Render e o banco Supabase acordados (anti-repouso)."""
    conexao = None
    try:
        conexao = pegar_conexao()
        conexao.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
    finally:
        if conexao:
            conexao.close()

def obter_todas_turmas(conexao) -> list[str]:
    turmas_db = conexao.execute('''
        SELECT nome FROM turmas
        UNION
        SELECT DISTINCT turma as nome FROM alunos WHERE turma IS NOT NULL AND turma != ''
        ORDER BY nome ASC
    ''').fetchall()
    return [t['nome'] for t in turmas_db]


def obter_turmas_para_matricula(conexao) -> list[dict]:
    rows = conexao.execute('''
        SELECT t.nome, t.limite_alunos, COUNT(a.id) AS total_alunos
        FROM turmas t
        LEFT JOIN alunos a ON a.turma = t.nome
        GROUP BY t.id, t.nome, t.limite_alunos
        ORDER BY t.nome ASC
    ''').fetchall()
    return [
        {
            **dict(row),
            "lotada": row["limite_alunos"] is not None and row["total_alunos"] >= row["limite_alunos"],
        }
        for row in rows
    ]

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
    conexao = None
    try:
        conexao = pegar_conexao()
        conexao.execute(
            "INSERT INTO logs_auditoria (usuario, acao, alvo, data_hora) VALUES (?, ?, ?, ?)",
            (usuario, acao, alvo, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conexao.commit()
    except Exception as e:
        logger.exception("Falha ao registrar auditoria")
    finally:
        if conexao:
            conexao.close()

# --- UTILITÁRIOS DE CRIPTOGRAFIA, SENHA FORTE & JWT ---
SENHAS_COMUNS_BLOQUEADAS = {
    "12345678", "123456789", "1234567890", "password", "password1", "password123",
    "senha123", "senha@123", "admin123", "admin@123", "qwerty123", "qwertyuiop",
    "casadelio", "adra1234", "professor", "coordenacao"
}

def validar_senha_forte(senha: str, login: str = "") -> tuple[bool, str]:
    """
    Valida a força da senha segundo padrões internacionais (OWASP ASVS / NIST SP 800-63B):
    - Mínimo de 8 e máximo de 128 caracteres;
    - Pelo menos 1 letra maiúscula (A-Z);
    - Pelo menos 1 letra minúscula (a-z);
    - Pelo menos 1 número (0-9);
    - Pelo menos 1 caractere especial (!@#$%^&*...);
    - Bloqueio de senhas comuns vazadas ou idênticas ao login.
    """
    if not senha or len(senha) < 8:
        return False, "A senha deve ter no mínimo 8 caracteres."
    if len(senha) > 128:
        return False, "A senha deve ter no máximo 128 caracteres."
    if not re.search(r"[A-Z]", senha):
        return False, "A senha deve conter pelo menos uma letra maiúscula (A-Z)."
    if not re.search(r"[a-z]", senha):
        return False, "A senha deve conter pelo menos uma letra minúscula (a-z)."
    if not re.search(r"\d", senha):
        return False, "A senha deve conter pelo menos um número (0-9)."
    if not re.search(r"[^A-Za-z0-9\s]", senha):
        return False, "A senha deve conter pelo menos um caractere especial (ex: @, #, $, !, %, *)."
    
    senha_limpa = re.sub(r"[^a-z0-9]", "", senha.lower())
    if senha.lower() in SENHAS_COMUNS_BLOQUEADAS or senha_limpa in SENHAS_COMUNS_BLOQUEADAS:
        return False, "Esta senha é muito comum e fácil de adivinhar. Escolha uma combinação mais segura."
    if login and len(login.strip()) >= 3 and login.strip().lower() in senha.lower():
        return False, "Por segurança, a senha não pode conter o nome de usuário (login)."
    
    return True, ""

def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode('utf-8'), senha_hash.encode('utf-8'))
    except (ValueError, TypeError):
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
            conexao = pegar_conexao()
            usuario = conexao.execute("SELECT login, perfil FROM usuarios WHERE login = ?", (payload["sub"],)).fetchone()
            conexao.close()
            if usuario:
                return {"login": usuario["login"], "perfil": usuario["perfil"]}
            
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
    
    if (ALLOW_WEAK_DEV_PASSWORDS or len(senha) >= 12) and usuario and verificar_senha(senha, usuario['senha']):
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
            secure=COOKIE_SECURE,
            max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            samesite="lax"
        )
        return response
        
    conexao.close()
    return RedirectResponse(url="/login?erro=1", status_code=303)
# --- NOVO: DASHBOARD ANALÍTICO (Apenas Coordenação) ---

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, pagina_risco: int = 1):
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
    
    # Alerta de faltas paginado, ordenado pelos maiores totais.
    itens_por_pagina_risco = 5
    total_alunos_risco = conexao.execute('''
        SELECT COUNT(DISTINCT aluno_id)
        FROM presencas
        WHERE status = 'Falta'
    ''').fetchone()[0]
    total_paginas_risco = max(1, (total_alunos_risco + itens_por_pagina_risco - 1) // itens_por_pagina_risco)
    pagina_risco = max(1, min(pagina_risco, total_paginas_risco))
    deslocamento_risco = (pagina_risco - 1) * itens_por_pagina_risco
    alunos_risco_db = conexao.execute('''
        SELECT alunos.nome, alunos.turma, alunos.telefone, COUNT(presencas.id) as total_faltas
        FROM presencas
        JOIN alunos ON presencas.aluno_id = alunos.id
        WHERE presencas.status = 'Falta'
        GROUP BY alunos.id, alunos.nome, alunos.turma, alunos.telefone
        ORDER BY total_faltas DESC, LOWER(alunos.nome) ASC, alunos.id ASC
        LIMIT ? OFFSET ?
    ''', (itens_por_pagina_risco, deslocamento_risco)).fetchall()
    
    # Prepara os números de telefone removendo traços e espaços para o WhatsApp
    alunos_risco = []
    for aluno in alunos_risco_db:
        aluno_dict = dict(aluno)
        telefone_limpo = ''.join(filter(str.isdigit, aluno_dict['telefone'] or ''))
        if len(telefone_limpo) in (10, 11):
            telefone_limpo = "55" + telefone_limpo # Adiciona o DDI do Brasil automaticamente
        aluno_dict['telefone_whatsapp'] = telefone_limpo
        if telefone_limpo:
            texto_whatsapp = f"Olá, notamos que o/a {aluno_dict['nome']} faltou às últimas aulas na Casa de Lió. Gostaríamos de saber se está tudo bem e se precisam de apoio."
            aluno_dict['whatsapp_url'] = f"https://wa.me/{telefone_limpo}?{urlencode({'text': texto_whatsapp})}"
        else:
            aluno_dict['whatsapp_url'] = ""
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
            "dados_turmas": dados_turmas, "alunos_risco": alunos_risco,
            "pagina_risco": pagina_risco, "total_paginas_risco": total_paginas_risco,
            "total_alunos_risco": total_alunos_risco
        }
    )
      
@app.post("/sair")
async def sair(request: Request):
    request.session.clear()
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="access_token", secure=COOKIE_SECURE, httponly=True, samesite="lax")
    return response

# --- ROTAS PROTEGIDAS ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, data_chamada: str = None, turma: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
        
    data_atual = data_chamada if data_chamada else date.today().isoformat()
    try:
        datetime.strptime(data_atual, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="Data inválida; use AAAA-MM-DD")
        
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
    aluno_existe = conexao.execute("SELECT 1 FROM alunos WHERE id = ?", (aluno_id,)).fetchone()
    if not aluno_existe:
        conexao.close()
        raise HTTPException(status_code=404, detail="Aluno não encontrado")
    if usuario['perfil'] != 'coordenacao':
        aluno_check = conexao.execute('SELECT turma FROM alunos WHERE id = ?', (aluno_id,)).fetchone()
        turmas_perm = obter_turmas_permitidas(conexao, usuario)
        if not aluno_check or aluno_check['turma'] not in turmas_perm:
            conexao.close()
            return RedirectResponse(url="/", status_code=303)

    try:
        datetime.strptime(data_chamada, "%Y-%m-%d")
    except ValueError:
        conexao.close()
        raise HTTPException(status_code=422, detail="Data inválida")
    conteudo = await arquivo.read(MAX_UPLOAD_BYTES + 1)
    if not conteudo or len(conteudo) > MAX_UPLOAD_BYTES:
        conexao.close()
        raise HTTPException(status_code=413, detail="O arquivo deve ter até 25 MB")
    formatos = ((b"%PDF-", ".pdf", "application/pdf"), (b"\xff\xd8\xff", ".jpg", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", ".png", "image/png"), (b"GIF87a", ".gif", "image/gif"), (b"GIF89a", ".gif", "image/gif"), (b"RIFF", ".webp", "image/webp"))
    formato = next((item for item in formatos if conteudo.startswith(item[0]) and (item[1] != ".webp" or conteudo[8:12] == b"WEBP")), None)
    if not formato:
        conexao.close()
        raise HTTPException(status_code=415, detail="Envie um PDF ou imagem válida (JPG, PNG, GIF ou WebP)")
    nome_seguro = f"atestado_{aluno_id}_{uuid.uuid4().hex}{formato[1]}"
    salvar_arquivo(conteudo, nome_seguro, content_type=formato[2])
        
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

@app.post("/registrar/{aluno_id}/{data_chamada}/{status}")
async def registrar(request: Request, aluno_id: int, data_chamada: str, status: str, turma: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
        
    if status not in {"Presente", "Falta", "Falta justificada"}:
        raise HTTPException(status_code=422, detail="Status inválido")
    try:
        datetime.strptime(data_chamada, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="Data inválida")
    conexao = pegar_conexao()
    if not conexao.execute("SELECT 1 FROM alunos WHERE id = ?", (aluno_id,)).fetchone():
        conexao.close()
        raise HTTPException(status_code=404, detail="Aluno não encontrado")
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


@app.post("/registrar_todos_presentes/{data_chamada}")
async def registrar_todos_presentes(request: Request, data_chamada: str, turma: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)

    try:
        datetime.strptime(data_chamada, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="Data inválida")

    conexao = pegar_conexao()
    turmas_perm = obter_turmas_permitidas(conexao, usuario)

    if turma:
        if turma not in turmas_perm:
            conexao.close()
            return RedirectResponse(url="/", status_code=303)
        alunos_alvo = conexao.execute("SELECT id FROM alunos WHERE turma = ?", (turma,)).fetchall()
    elif usuario['perfil'] != 'coordenacao':
        if not turmas_perm:
            conexao.close()
            return RedirectResponse(url="/", status_code=303)
        placeholders = ','.join(['?'] * len(turmas_perm))
        alunos_alvo = conexao.execute(f"SELECT id FROM alunos WHERE turma IN ({placeholders})", turmas_perm).fetchall()
    else:
        alunos_alvo = conexao.execute("SELECT id FROM alunos").fetchall()

    for row in alunos_alvo:
        aid = row['id']
        existe = conexao.execute(
            "SELECT id, status, atestado FROM presencas WHERE aluno_id = ? AND data = ?",
            (aid, data_chamada),
        ).fetchone()
        if existe:
            # Preserva falta justificada que já possua atestado médico anexado
            if existe['atestado'] and existe['status'] == 'Falta justificada':
                continue
            conexao.execute("UPDATE presencas SET status = ? WHERE id = ?", ("Presente", existe['id']))
        else:
            conexao.execute(
                "INSERT INTO presencas (aluno_id, data, status) VALUES (?, ?, ?)",
                (aid, data_chamada, "Presente"),
            )

    conexao.commit()
    conexao.close()

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
    if usuario.get("perfil") != "coordenacao" and turma and turma not in turmas_perm:
        conexao.close()
        raise HTTPException(status_code=403, detail="Sem acesso a esta turma")
    
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
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mes):
            conexao.close()
            raise HTTPException(status_code=422, detail="Mês inválido; use AAAA-MM")
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
    
    buffer_csv = io.StringIO(newline="")
    writer = csv.writer(buffer_csv, delimiter=";")
    writer.writerow(["Nome", "Turma", "Data", "Status"])
    for linha in relatorio:
        try:
            data_br = datetime.strptime(linha['data'], '%Y-%m-%d').strftime('%d/%m/%Y')
        except (TypeError, ValueError):
            data_br = linha['data'] or ""
        valores = [linha['nome'], linha['turma'], data_br, linha['status']]
        writer.writerow([("'" + str(v) if str(v).startswith(("=", "+", "-", "@")) else v) for v in valores])
        
    return Response(
        content=buffer_csv.getvalue().encode('utf-8-sig'),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=relatorio_presencas.csv"}
    )
    
@app.get("/cadastrar", response_class=HTMLResponse)
async def tela_cadastrar(request: Request, erro: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
    conexao = pegar_conexao()
    turmas_disponiveis = obter_turmas_para_matricula(conexao)
    conexao.close()
    return templates.TemplateResponse(request=request, name="cadastro.html", context={"turmas_disponiveis": turmas_disponiveis, "erro": erro})

# NOVO: A rota invisível que recebe os dados do formulário e salva no banco
@app.post("/cadastrar")
async def salvar_cadastro(request: Request):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    
    nome = (formulario.get('nome') or '').strip()
    turma = (formulario.get('turma') or '').strip()
    telefone = (formulario.get('telefone') or '').strip()
    
    conexao = pegar_conexao()
    conexao.execute("BEGIN IMMEDIATE")
    cursor = conexao.cursor()
    turma_db = cursor.execute("SELECT limite_alunos FROM turmas WHERE nome = ?", (turma,)).fetchone()
    if not turma_db:
        conexao.rollback()
        conexao.close()
        raise HTTPException(status_code=422, detail="Selecione uma turma cadastrada")
    matriculados = cursor.execute("SELECT COUNT(*) FROM alunos WHERE turma = ?", (turma,)).fetchone()[0]
    if turma_db["limite_alunos"] is not None and matriculados >= turma_db["limite_alunos"]:
        conexao.rollback()
        conexao.close()
        return RedirectResponse(url="/cadastrar?erro=turma_lotada", status_code=303)

    cursor.execute('''
        INSERT INTO alunos (nome, turma, telefone) VALUES (?, ?, ?)
    ''', (nome, turma, telefone))
    
    aluno_id = cursor.lastrowid # Capta o ID exato do aluno que acabou de ser gravado
    conexao.commit()
    conexao.close()
    
    # Registro de auditoria LGPD
    registrar_auditoria(usuario['login'], "CADASTRO_ALUNO", f"Matrícula: {nome} (ID: {aluno_id}, Turma: {turma}, Tel: {telefone})")
    
    # Redireciona para a ficha do aluno recém-matriculado
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
    if not aluno:
        raise HTTPException(status_code=404, detail="Aluno não encontrado")
    return templates.TemplateResponse(request=request, name="ficha.html", context={"aluno": aluno})

@app.get("/usuarios/novo", response_class=HTMLResponse)
async def tela_novo_usuario(request: Request, sucesso: str = None, erro: str = None):
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
            "sucesso": sucesso,
            "erro": erro
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
            senha_valida, _ = validar_senha_forte(nova_senha, alvo['login'])
            if not senha_valida:
                conexao.close()
                return RedirectResponse(url="/usuarios/novo?erro=senha_fraca", status_code=303)

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
    login = (formulario.get('login') or '').strip()
    senha = formulario.get('senha') or ''
    perfil = formulario.get('perfil')
    if not login or len(login) > 80 or perfil not in {"professor", "coordenacao"}:
        raise HTTPException(status_code=422, detail="Informe login e perfil válidos")

    senha_valida, _ = validar_senha_forte(senha, login)
    if not senha_valida:
        return RedirectResponse(url="/usuarios/novo?erro=senha_fraca", status_code=303)
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
    except sqlite3.IntegrityError:
        conexao.rollback()
        return RedirectResponse(url="/usuarios/novo?sucesso=login_existente", status_code=303)
    except Exception:
        conexao.rollback()
        logger.exception("Falha ao criar usuário")
        raise HTTPException(status_code=500, detail="Não foi possível criar o usuário")
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
async def tela_editar_aluno(request: Request, aluno_id: int, erro: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    aluno = conexao.execute('SELECT * FROM alunos WHERE id = ?', (aluno_id,)).fetchone()
    turmas_disponiveis = obter_turmas_para_matricula(conexao)
    conexao.close()
    
    if not aluno:
        return RedirectResponse(url="/alunos", status_code=303)
        
    return templates.TemplateResponse(
        request=request,
        name="editar_aluno.html",
        context={"aluno": aluno, "turmas_disponiveis": turmas_disponiveis, "erro": erro}
    )

@app.post("/alunos/editar/{aluno_id}")
async def salvar_edicao_aluno(request: Request, aluno_id: int):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    nome = (formulario.get('nome') or '').strip()
    turma = (formulario.get('turma') or '').strip()
    telefone = (formulario.get('telefone') or '').strip()
    
    conexao = pegar_conexao()
    conexao.execute("BEGIN IMMEDIATE")
    aluno_atual = conexao.execute("SELECT turma FROM alunos WHERE id = ?", (aluno_id,)).fetchone()
    if not aluno_atual:
        conexao.rollback()
        conexao.close()
        raise HTTPException(status_code=404, detail="Aluno não encontrado")
    turma_db = conexao.execute("SELECT limite_alunos FROM turmas WHERE nome = ?", (turma,)).fetchone()
    if not turma_db:
        conexao.rollback()
        conexao.close()
        return RedirectResponse(url=f"/alunos/editar/{aluno_id}?erro=turma_invalida", status_code=303)
    if turma != aluno_atual['turma'] and turma_db['limite_alunos'] is not None:
        matriculados = conexao.execute("SELECT COUNT(*) FROM alunos WHERE turma = ?", (turma,)).fetchone()[0]
        if matriculados >= turma_db['limite_alunos']:
            conexao.rollback()
            conexao.close()
            return RedirectResponse(url=f"/alunos/editar/{aluno_id}?erro=turma_lotada", status_code=303)

    conexao.execute('''
        UPDATE alunos SET nome = ?, turma = ?, telefone = ? WHERE id = ?
    ''', (nome, turma, telefone, aluno_id))
    conexao.commit()
    conexao.close()
    
    registrar_auditoria(usuario['login'], "EDICAO_ALUNO", f"Aluno atualizado: {nome} (ID: {aluno_id}, Turma: {turma}, Tel: {telefone})")
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
        SELECT t.id, t.nome, t.descricao, t.limite_alunos,
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
            "limite_alunos": t['limite_alunos'],
            "ocupacao_percentual": min(100, round(t['total_alunos'] * 100 / t['limite_alunos'])) if t['limite_alunos'] else 0,
            "lotada": t['limite_alunos'] is not None and t['total_alunos'] >= t['limite_alunos'],
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

@app.get("/turmas/nova", response_class=HTMLResponse)
async def tela_nova_turma(request: Request, erro: str = None):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    profs_db = conexao.execute("SELECT id, login FROM usuarios WHERE perfil = 'professor' ORDER BY login ASC").fetchall()
    professores_sistema = [{"id": p['id'], "login": p['login']} for p in profs_db]
    conexao.close()
    return templates.TemplateResponse(
        request=request,
        name="nova_turma.html",
        context={
            "professores": professores_sistema,
            "usuario_atual": usuario,
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
    limite_bruto = (formulario.get('limite_alunos') or '').strip()
    try:
        limite_alunos = int(limite_bruto) if limite_bruto else None
        if limite_alunos is not None and limite_alunos < 1:
            raise ValueError
    except ValueError:
        return RedirectResponse(url="/turmas?erro=limite_invalido", status_code=303)
    if hasattr(formulario, 'getlist'):
        professores_ids = formulario.getlist('professores')
    else:
        val_p = formulario.get('professores', [])
        professores_ids = val_p if isinstance(val_p, list) else ([val_p] if val_p else [])
        
    if nome:
        conexao = pegar_conexao()
        cursor = conexao.cursor()
        duplicada = cursor.execute("SELECT 1 FROM turmas WHERE nome = ?", (nome,)).fetchone()
        if duplicada:
            conexao.close()
            return RedirectResponse(url="/turmas?erro=nome_duplicado", status_code=303)
        cursor.execute("INSERT INTO turmas (nome, descricao, limite_alunos) VALUES (?, ?, ?)", (nome, descricao, limite_alunos))
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
        
    return RedirectResponse(url="/turmas?sucesso=criada", status_code=303)

@app.post("/turmas/vincular")
async def vincular_professor_turmas(request: Request):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    usuario_id = formulario.get('usuario_id')
    origem = formulario.get('origem', 'usuarios')
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
        return RedirectResponse(url="/usuarios/novo?sucesso=turmas", status_code=303)
    return RedirectResponse(url="/turmas", status_code=303)

@app.post("/turmas/editar/{turma_id}")
async def editar_turma(request: Request, turma_id: int):
    usuario = obter_usuario_atual(request)
    if not usuario or usuario['perfil'] != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    novo_nome = (formulario.get('nome') or '').strip()
    nova_descricao = (formulario.get('descricao') or '').strip()
    limite_bruto = (formulario.get('limite_alunos') or '').strip()
    try:
        novo_limite = int(limite_bruto) if limite_bruto else None
        if novo_limite is not None and novo_limite < 1:
            raise ValueError
    except ValueError:
        return RedirectResponse(url="/turmas?erro=limite_invalido", status_code=303)
    if hasattr(formulario, 'getlist'):
        professores_ids = formulario.getlist('professores')
    else:
        val_p = formulario.get('professores', [])
        professores_ids = val_p if isinstance(val_p, list) else ([val_p] if val_p else [])
        
    if not novo_nome:
        return RedirectResponse(url="/turmas", status_code=303)
        
    conexao = pegar_conexao()
    cursor = conexao.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    turma_atual = cursor.execute("SELECT id, nome, descricao, limite_alunos FROM turmas WHERE id = ?", (turma_id,)).fetchone()
    if not turma_atual:
        conexao.rollback()
        conexao.close()
        return RedirectResponse(url="/turmas", status_code=303)
        
    nome_antigo = turma_atual['nome']
    matriculados_turma = cursor.execute("SELECT COUNT(*) FROM alunos WHERE turma = ?", (nome_antigo,)).fetchone()[0]
    if novo_limite is not None and novo_limite < matriculados_turma:
        conexao.rollback()
        conexao.close()
        return RedirectResponse(url="/turmas?erro=limite_menor_que_ocupacao", status_code=303)
    
    # Se o nome mudou, verifica se já não existe outra turma com o novo nome
    if novo_nome != nome_antigo:
        conflito = cursor.execute("SELECT id FROM turmas WHERE nome = ? AND id != ?", (novo_nome, turma_id)).fetchone()
        if conflito:
            conexao.rollback()
            conexao.close()
            return RedirectResponse(url="/turmas?erro=nome_duplicado", status_code=303)
            
    try:
        # 1. Atualiza registro na tabela turmas
        cursor.execute("UPDATE turmas SET nome = ?, descricao = ?, limite_alunos = ? WHERE id = ?", (novo_nome, nova_descricao, novo_limite, turma_id))
        
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
