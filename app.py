from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import sqlite3
from datetime import date, datetime
import os
import shutil

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="chave_secreta_casa_de_lio")
templates = Jinja2Templates(directory="templates")

# NOVO: Garante que a pasta 'uploads' existe e permite que o navegador mostre os arquivos
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

def pegar_conexao():
    conexao = sqlite3.connect('banco.db')
    conexao.row_factory = sqlite3.Row
    return conexao

# --- SISTEMA DE LOGIN ---
@app.get("/login", response_class=HTMLResponse)
async def tela_login(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")

@app.post("/login")
async def fazer_login(request: Request, login: str = Form(...), senha: str = Form(...)):
    conexao = pegar_conexao()
    usuario = conexao.execute('SELECT * FROM usuarios WHERE login = ? AND senha = ?', (login, senha)).fetchone()
    conexao.close()
    
    if usuario:
        request.session['perfil'] = usuario['perfil']
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url="/login", status_code=303)
# --- NOVO: DASHBOARD ANALÍTICO (Apenas Coordenação) ---

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if request.session.get('perfil') != 'coordenacao':
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
    return RedirectResponse(url="/login", status_code=303)

# --- ROTAS PROTEGIDAS ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, data_chamada: str = None):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
        
    data_atual = data_chamada if data_chamada else date.today().isoformat()
        
    conexao = pegar_conexao()
    # ATUALIZAÇÃO: Agora o SQL também puxa a coluna 'atestado'
    alunos = conexao.execute('''
        SELECT a.id, a.nome, a.turma, p.status, p.atestado,
               (SELECT COUNT(*) FROM presencas WHERE aluno_id = a.id AND status = 'Falta') as total_faltas
        FROM alunos a
        LEFT JOIN presencas p ON a.id = p.aluno_id AND p.data = ?
    ''', (data_atual,)).fetchall()
    conexao.close()
    
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"alunos": alunos, "data_atual": data_atual}
    )

# NOVO: Rota que recebe o arquivo PDF/Imagem e justifica a falta automaticamente
@app.post("/anexar_atestado/{aluno_id}/{data_chamada}")
async def anexar_atestado(request: Request, aluno_id: int, data_chamada: str, arquivo: UploadFile = File(...)):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
        
    # Limpa o nome do arquivo e salva na pasta uploads
    nome_seguro = f"atestado_{aluno_id}_{data_chamada}_{arquivo.filename.replace(' ', '_')}"
    caminho_salvar = f"uploads/{nome_seguro}"
    
    with open(caminho_salvar, "wb") as buffer:
        shutil.copyfileobj(arquivo.file, buffer)
        
    conexao = pegar_conexao()
    existe = conexao.execute('SELECT id FROM presencas WHERE aluno_id = ? AND data = ?', (aluno_id, data_chamada)).fetchone()
    
    # Automatiza a Justificativa marcando 'Falta justificada' e anexando o arquivo
    if existe:
        conexao.execute('UPDATE presencas SET status = ?, atestado = ? WHERE id = ?', ('Falta justificada', nome_seguro, existe['id']))
    else:
        conexao.execute('INSERT INTO presencas (aluno_id, data, status, atestado) VALUES (?, ?, ?, ?)', (aluno_id, data_chamada, 'Falta justificada', nome_seguro))
        
    conexao.commit()
    conexao.close()
    
    # Devolve a tela atualizada
    return RedirectResponse(url=f"/?data_chamada={data_chamada}", status_code=303)

@app.get("/registrar/{aluno_id}/{data_chamada}/{status}")
async def registrar(request: Request, aluno_id: int, data_chamada: str, status: str):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
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
    
    # Devolve a professora para a MESMA data que estava a editar
    return RedirectResponse(url=f"/?data_chamada={data_chamada}", status_code=303)

@app.get("/exportar")
async def exportar(request: Request, mes: str = None, turma: str = None):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    
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
    if request.session.get('perfil') != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="cadastro.html")

# NOVO: A rota invisível que recebe os dados do formulário e salva no banco
@app.post("/cadastrar")
async def salvar_cadastro(request: Request):
    if request.session.get('perfil') != 'coordenacao':
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
    
    conexao = pegar_conexao()
    cursor = conexao.cursor()
    cursor.execute('''
        INSERT INTO alunos (
            nome, data_nascimento, turma, telefone, 
            cep, rua, numero, bairro, cidade, referencia,
            contato_emergencia, telefone_emergencia, condicao_medica,
            nome_responsavel, autorizacao_pais
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (nome, data_nascimento, turma, telefone, cep, rua, numero, bairro, cidade, referencia, 
          contato_emergencia, telefone_emergencia, condicao_medica, nome_responsavel, autorizacao))
    
    aluno_id = cursor.lastrowid # Capta o ID exato do aluno que acabou de ser gravado
    conexao.commit()
    conexao.close()
    
    # NOVO: Em vez do Dashboard, atira para a ficha de impressão
    return RedirectResponse(url=f"/ficha/{aluno_id}", status_code=303)

# NOVO: Rota exclusiva para gerar a ficha em tamanho A4
@app.get("/ficha/{aluno_id}", response_class=HTMLResponse)
async def gerar_ficha(request: Request, aluno_id: int):
    if request.session.get('perfil') != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    aluno = conexao.execute('SELECT * FROM alunos WHERE id = ?', (aluno_id,)).fetchone()
    conexao.close()
    
    return templates.TemplateResponse(request=request, name="ficha.html", context={"aluno": aluno})

@app.post("/cadastrar")
async def salvar_cadastro(request: Request):
    if request.session.get('perfil') != 'coordenacao':
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
    
    conexao = pegar_conexao()
    cursor = conexao.cursor()
    cursor.execute('''
        INSERT INTO alunos (
            nome, data_nascimento, turma, telefone, 
            cep, rua, numero, bairro, cidade, referencia,
            contato_emergencia, telefone_emergencia, condicao_medica,
            nome_responsavel, autorizacao_pais
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (nome, data_nascimento, turma, telefone, cep, rua, numero, bairro, cidade, referencia, 
          contato_emergencia, telefone_emergencia, condicao_medica, nome_responsavel, autorizacao))
    
    aluno_id = cursor.lastrowid # Capta o ID exato do aluno que acabou de ser gravado
    conexao.commit()
    conexao.close()
    
    # NOVO: Em vez do Dashboard, atira para a ficha de impressão
    return RedirectResponse(url=f"/ficha/{aluno_id}", status_code=303)

# NOVO: Rota exclusiva para gerar a ficha em tamanho A4
@app.get("/ficha/{aluno_id}", response_class=HTMLResponse)
async def gerar_ficha(request: Request, aluno_id: int):
    if request.session.get('perfil') != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    aluno = conexao.execute('SELECT * FROM alunos WHERE id = ?', (aluno_id,)).fetchone()
    conexao.close()
    
    return templates.TemplateResponse(request=request, name="ficha.html", context={"aluno": aluno})

@app.get("/usuarios/novo", response_class=HTMLResponse)
async def tela_novo_usuario(request: Request):
    # Proteção estrita: Apenas a coordenação pode aceder
    if request.session.get('perfil') != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="novo_usuario.html")

@app.post("/usuarios/novo")
async def salvar_usuario(request: Request):
    # Proteção estrita na gravação dos dados
    if request.session.get('perfil') != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    formulario = await request.form()
    login = formulario.get('login')
    senha = formulario.get('senha')
    perfil = formulario.get('perfil')
    
    conexao = pegar_conexao()
    try:
        conexao.execute('''
            INSERT INTO usuarios (login, senha, perfil) 
            VALUES (?, ?, ?)
        ''', (login, senha, perfil))
        conexao.commit()
    except:
        # Se o utilizador já existir (o login é UNIQUE), o sistema ignora o erro para não quebrar
        pass 
    finally:
        conexao.close()
        
    # Após criar a conta, devolve a coordenação ao Painel Analítico
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/alunos", response_class=HTMLResponse)
async def listar_alunos(request: Request):
    # Proteção: Apenas a coordenação pode ver o diretório completo
    if request.session.get('perfil') != 'coordenacao':
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
    
    # NOVO: Rota que recebe o arquivo PDF/Imagem e justifica a falta automaticamente
@app.post("/anexar_atestado/{aluno_id}/{data_chamada}")
async def anexar_atestado(request: Request, aluno_id: int, data_chamada: str, arquivo: UploadFile = File(...)):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
        
    # Limpa o nome do arquivo e salva na pasta uploads
    nome_seguro = f"atestado_{aluno_id}_{data_chamada}_{arquivo.filename.replace(' ', '_')}"
    caminho_salvar = f"uploads/{nome_seguro}"
    
    with open(caminho_salvar, "wb") as buffer:
        shutil.copyfileobj(arquivo.file, buffer)
        
    conexao = pegar_conexao()
    existe = conexao.execute('SELECT id FROM presencas WHERE aluno_id = ? AND data = ?', (aluno_id, data_chamada)).fetchone()
    
    # Automatiza a Justificativa marcando 'Falta justificada' e anexando o arquivo
    if existe:
        conexao.execute('UPDATE presencas SET status = ?, atestado = ? WHERE id = ?', ('Falta justificada', nome_seguro, existe['id']))
    else:
        conexao.execute('INSERT INTO presencas (aluno_id, data, status, atestado) VALUES (?, ?, ?, ?)', (aluno_id, data_chamada, 'Falta justificada', nome_seguro))
        
    conexao.commit()
    conexao.close()
    
    # Devolve a tela atualizada preservando a data escolhida
    return RedirectResponse(url=f"/?data_chamada={data_chamada}", status_code=303)