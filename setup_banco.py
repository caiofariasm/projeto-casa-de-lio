import sqlite3
from pathlib import Path
import bcrypt
import os
from dotenv import load_dotenv
from catalogo_turmas import TURMAS_INICIAIS

def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

DB_PATH = Path(__file__).resolve().parent / 'banco.db'
load_dotenv(Path(__file__).resolve().parent / ".env")
conexao = sqlite3.connect(str(DB_PATH))
cursor = conexao.cursor()

# 1. Tabela de Alunos (Estrutura Completa da Ficha de Matrícula)
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

# 2. Tabela de Presenças (Com suporte a upload de atestados)
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

# 3. Tabela de Usuários (Controle de Acesso da Coordenação e Professores)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        login TEXT UNIQUE NOT NULL,
        senha TEXT NOT NULL,
        perfil TEXT NOT NULL
    )
''')

# 4. Tabela de Auditoria e Segurança (Conformidade LGPD - Trilha de Acessos e Ações)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS logs_auditoria (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario TEXT NOT NULL,
        acao TEXT NOT NULL,
        alvo TEXT,
        data_hora TEXT NOT NULL
    )
''')

# 5. Tabela de Turmas / Cursos e Vínculo com Professores
cursor.execute('''
    CREATE TABLE IF NOT EXISTS turmas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT UNIQUE NOT NULL,
        descricao TEXT,
        limite_alunos INTEGER CHECK (limite_alunos IS NULL OR limite_alunos > 0)
    )
''')
colunas_turmas = {col[1] for col in cursor.execute("PRAGMA table_info(turmas)").fetchall()}
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

# 6. Criar o utilizador de coordenação padrão (com senha hasheada via variável de ambiente)
senha_admin = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
ambiente_dev = os.getenv("ENVIRONMENT", "development").strip().lower() == "development"
if senha_admin:
    if len(senha_admin) < 12 and not ambiente_dev:
        raise SystemExit("BOOTSTRAP_ADMIN_PASSWORD deve ter ao menos 12 caracteres")
    senha_padrao = hash_senha(senha_admin)
    # Acesso Total (Coordenação)
    cursor.execute("SELECT id FROM usuarios WHERE login = 'coord'")
    admin = cursor.fetchone()
    if admin:
        cursor.execute("UPDATE usuarios SET senha = ?, perfil = 'coordenacao' WHERE id = ?", (senha_padrao, admin[0]))
    else:
        cursor.execute("INSERT INTO usuarios (login, senha, perfil) VALUES ('coord', ?, 'coordenacao')", (senha_padrao,))

conexao.commit()
conexao.close()
print("[OK] Esquema preparado. Nenhuma senha padrao foi criada.")
