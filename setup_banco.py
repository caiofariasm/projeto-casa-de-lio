import sqlite3

conexao = sqlite3.connect('banco.db')
cursor = conexao.cursor()

# 1. Tabela de Alunos (Estrutura CRM Completa)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS alunos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        data_nascimento TEXT NOT NULL,
        turma TEXT NOT NULL,
        telefone TEXT NOT NULL,
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
        autorizacao_pais BOOLEAN
    )
''')


# 2. Tabela de Presenças
cursor.execute('''
    CREATE TABLE IF NOT EXISTS presencas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        aluno_id INTEGER,
        data TEXT NOT NULL,
        status TEXT NOT NULL,
        FOREIGN KEY (aluno_id) REFERENCES alunos (id)
    )
''')

# 3. Tabela de Utilizadores (A que estava a faltar!)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        login TEXT UNIQUE NOT NULL,
        senha TEXT NOT NULL,
        perfil TEXT NOT NULL
    )
''')

# Cria as contas padrão automaticamente para não ficares trancado fora do sistema
cursor.execute("INSERT OR IGNORE INTO usuarios (login, senha, perfil) VALUES ('coord', 'admin', 'coordenacao')")
cursor.execute("INSERT OR IGNORE INTO usuarios (login, senha, perfil) VALUES ('prof', '123', 'professor')")

conexao.commit()
conexao.close()

print("Banco de dados recriado com sucesso! (Alunos, Presenças e Usuários)")