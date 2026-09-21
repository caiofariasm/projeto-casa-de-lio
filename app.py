from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from datetime import date
from datetime import date, datetime
import sqlite3

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="chave_secreta_casa_de_lio")
templates = Jinja2Templates(directory="templates")

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
    
    # 1. Total de alunos cadastrados
    total_alunos = conexao.execute('SELECT COUNT(*) as qtd FROM alunos').fetchone()['qtd']
    
    # 2. TOTAIS PARA OS CARTÕES SUPERIORES (A variável que estava em falta!)
    totais = conexao.execute('SELECT status, COUNT(*) as quantidade FROM presencas GROUP BY status').fetchall()
    dados_grafico = {"Presente": 0, "Falta": 0, "Falta justificada": 0}
    for linha in totais:
        dados_grafico[linha['status']] = linha['quantidade']
    
    # 3. HISTÓRICO PARA O GRÁFICO DE EVOLUÇÃO
    historico = conexao.execute('''
        SELECT data,
               SUM(CASE WHEN status = 'Presente' THEN 1 ELSE 0 END) as presentes,
               SUM(CASE WHEN status = 'Falta' THEN 1 ELSE 0 END) as faltas,
               SUM(CASE WHEN status = 'Falta justificada' THEN 1 ELSE 0 END) as justificadas
        FROM presencas
        GROUP BY data
        ORDER BY data ASC
    ''').fetchall()
    conexao.close()
    
    datas = [datetime.strptime(linha['data'], '%Y-%m-%d').strftime('%d/%m/%Y') for linha in historico]
    presentes = [linha['presentes'] for linha in historico]
    faltas = [linha['faltas'] for linha in historico]
    justificadas = [linha['justificadas'] for linha in historico]
    
    return templates.TemplateResponse(
        request=request, 
        name="dashboard.html", 
        context={
            "total_alunos": total_alunos, 
            "dados_grafico": dados_grafico, # <-- A variável voltou!
            "datas": datas, 
            "presentes": presentes, 
            "faltas": faltas,
            "justificadas": justificadas
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
        
    # Se a professora não escolheu uma data, usa o dia de hoje
    data_atual = data_chamada if data_chamada else date.today().isoformat()
        
    conexao = pegar_conexao()
    # LEFT JOIN: Traz sempre todos os alunos. Se houver registo nessa data, traz o status; se não, traz nulo.
    alunos = conexao.execute('''
        SELECT a.id, a.nome, a.turma, p.status
        FROM alunos a
        LEFT JOIN presencas p ON a.id = p.aluno_id AND p.data = ?
    ''', (data_atual,)).fetchall()
    conexao.close()
    
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"alunos": alunos, "data_atual": data_atual}
    )

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
async def exportar(request: Request):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    # Melhoria de Ordenação: Traz os dados organizados da data mais recente para a mais antiga
    relatorio = conexao.execute('''
        SELECT alunos.nome, alunos.turma, presencas.data, presencas.status 
        FROM presencas
        JOIN alunos ON presencas.aluno_id = alunos.id
        ORDER BY presencas.data DESC, alunos.nome ASC
    ''').fetchall()
    conexao.close()
    
    # Melhoria do Separador: Uso do ponto e vírgula (;) para o Excel em português ler as colunas perfeitamente
    texto_csv = "Nome;Turma;Data;Status\n"
    for linha in relatorio:
        # Aproveitamos a lógica que criaste antes para garantir o formato Dia/Mês/Ano
        data_br = datetime.strptime(linha['data'], '%Y-%m-%d').strftime('%d/%m/%Y')
        texto_csv += f"{linha['nome']};{linha['turma']};{data_br};{linha['status']}\n"
        
    return Response(
        content=texto_csv.encode('utf-8-sig'),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=relatorio_presencas.csv"}
    )
    
@app.get("/cadastrar", response_class=HTMLResponse)
async def tela_cadastrar(request: Request):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request=request, name="cadastro.html")

@app.post("/cadastrar")
async def salvar_aluno(request: Request, nome: str = Form(...), turma: str = Form(...)):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    conexao.execute('INSERT INTO alunos (nome, turma) VALUES (?, ?)', (nome, turma))
    conexao.commit()
    conexao.close()
    return RedirectResponse(url="/", status_code=303)