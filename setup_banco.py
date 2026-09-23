import sqlite3
from pathlib import Path
import bcrypt

def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

DB_PATH = Path(__file__).resolve().parent / 'banco.db'
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

# 5. Criar os utilizadores padrão (As Fechaduras do Sistema com senhas hasheadas)
cursor.execute('SELECT COUNT(*) FROM usuarios')
if cursor.fetchone()[0] == 0:
    senha_padrao = hash_senha('123')
    # Acesso Total
    cursor.execute("INSERT INTO usuarios (login, senha, perfil) VALUES ('coord', ?, 'coordenacao')", (senha_padrao,))
    # Acesso Restrito à Chamada
    cursor.execute("INSERT INTO usuarios (login, senha, perfil) VALUES ('prof', ?, 'professor')", (senha_padrao,))

conexao.commit()
conexao.close()
print("[OK] Banco de dados recriado com sucesso! Tabelas prontas e utilizadores gerados.")