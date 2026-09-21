from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
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
    # Bloqueia se não for coordenação
    if request.session.get('perfil') != 'coordenacao':
        return RedirectResponse(url="/", status_code=303)
        
    conexao = pegar_conexao()
    
    # 1. Conta o total de alunos cadastrados
    total_alunos = conexao.execute('SELECT COUNT(*) as qtd FROM alunos').fetchone()['qtd']
    
    # 2. Conta quantas presenças e quantas faltas existem no total
    totais = conexao.execute('SELECT status, COUNT(*) as quantidade FROM presencas GROUP BY status').fetchall()
    
    # Organiza os dados para enviar para o gráfico
    dados_grafico = {"Presente": 0, "Falta": 0}
    for linha in totais:
        dados_grafico[linha['status']] = linha['quantidade']
        
    conexao.close()
    
    return templates.TemplateResponse(
        request=request, 
        name="dashboard.html", 
        context={
            "total_alunos": total_alunos, 
            "dados_grafico": dados_grafico
        }
    )
@app.get("/sair")
async def sair(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

# --- ROTAS PROTEGIDAS ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    alunos_pendentes = conexao.execute('''
        SELECT * FROM alunos 
        WHERE id NOT IN (
            SELECT aluno_id FROM presencas WHERE data = CURRENT_DATE
        )
    ''').fetchall()
    conexao.close()
    
    return templates.TemplateResponse(request=request, name="index.html", context={"alunos": alunos_pendentes})

@app.get("/registrar/{aluno_id}/{status}")
async def registrar(request: Request, aluno_id: int, status: str):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    conexao.execute('INSERT INTO presencas (aluno_id, status) VALUES (?, ?)', (aluno_id, status))
    conexao.commit()
    conexao.close()
    return RedirectResponse(url="/", status_code=303)

@app.get("/exportar")
async def exportar(request: Request):
    if not request.session.get('perfil'):
        return RedirectResponse(url="/login", status_code=303)
        
    conexao = pegar_conexao()
    relatorio = conexao.execute('''
        SELECT alunos.nome, alunos.turma, presencas.data, presencas.status 
        FROM presencas
        JOIN alunos ON presencas.aluno_id = alunos.id
    ''').fetchall()
    conexao.close()
    
    texto_csv = "Nome,Turma,Data,Status\n"
    for linha in relatorio:
        texto_csv += f"{linha['nome']},{linha['turma']},{linha['data']},{linha['status']}\n"
        
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