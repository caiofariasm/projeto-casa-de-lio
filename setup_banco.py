import sqlite3

conexao = sqlite3.connect('banco.db')
cursor = conexao.cursor()

# Tabela de Alunos
cursor.execute('''
    CREATE TABLE IF NOT EXISTS alunos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        turma TEXT NOT NULL
    )
''')

# Tabela de Presenças Diárias
cursor.execute('''
    CREATE TABLE IF NOT EXISTS presencas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        aluno_id INTEGER,
        data DATE DEFAULT CURRENT_DATE,
        status TEXT NOT NULL,
        FOREIGN KEY(aluno_id) REFERENCES alunos(id)
    )
''')

# Tabela de Utilizadores (Controlo de Acesso)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        login TEXT UNIQUE NOT NULL,
        senha TEXT NOT NULL,
        perfil TEXT NOT NULL
    )
''')

# Dados Iniciais (Alunos e Utilizadores de teste)
cursor.execute("INSERT OR IGNORE INTO usuarios (login, senha, perfil) VALUES ('prof', '123', 'professor')")
cursor.execute("INSERT OR IGNORE INTO usuarios (login, senha, perfil) VALUES ('coord', 'admin', 'coordenacao')")
cursor.execute("INSERT INTO alunos (nome, turma) VALUES ('João Silva', 'Manhã')")
cursor.execute("INSERT INTO alunos (nome, turma) VALUES ('Maria Oliveira', 'Manhã')")

conexao.commit()
conexao.close()
print("Base de dados completa criada com sucesso!")