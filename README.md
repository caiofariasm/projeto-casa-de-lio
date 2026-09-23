# 🏫 Sistema de Gestão Escolar - Casa de Lió (ADRA)

![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-Modern%20Web%20Framework-009688.svg)
![Security](https://img.shields.io/badge/Security-JWT%20%7C%20Bcrypt-red.svg)
![Compliance](https://img.shields.io/badge/LGPD-Conforme%20Lei%2013.709%2F2018-green.svg)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)
![Nginx](https://img.shields.io/badge/Nginx-Reverse%20Proxy-009639.svg)
![Storage](https://img.shields.io/badge/Object%20Storage-MinIO%20%2F%20AWS%20S3-orange.svg)

Sistema web para controle de frequência escolar, prontuário cadastral, gestão de faltas e justificativas com laudos médicos, desenvolvido para o projeto social e educacional **Casa de Lió** mantido pela **ADRA (Agência Adventista de Desenvolvimento e Recursos Assistenciais)**.

---

## 📑 Sumário

1. [Visão Geral](#-visão-geral)
2. [Funcionalidades do Sistema](#-funcionalidades-do-sistema)
3. [Arquitetura de Software](#-arquitetura-de-software)
4. [Conformidade com a LGPD](#-conformidade-com-a-lgpd)
5. [Segurança e Autenticação](#-segurança-e-autenticação)
6. [Tecnologias Utilizadas](#-tecnologias-utilizadas)
7. [Estrutura do Projeto](#-estrutura-do-projeto)
8. [Como Executar](#-como-executar)
   - [Opção 1: Via Docker Compose (Recomendado)](#opção-1-execução-completa-com-docker-compose)
   - [Opção 2: Execução Local com Python](#opção-2-execução-local-direta)
9. [Variáveis de Ambiente](#-variáveis-de-ambiente)
10. [Mapeamento de Rotas e Endpoints](#-mapeamento-de-rotas-e-endpoints)
11. [Manuais Adicionais](#-manuais-adicionais)

---

## 🌟 Visão Geral

O sistema automatiza e profissionaliza os processos pedagógicos e administrativos da instituição:
* **Fim das planilhas manuais de papel:** Chamada diária digital rápida e acessível de computadores, tablets ou celulares.
* **Acompanhamento de Evasão Escolar:** Identificação precoce de alunos com excesso de faltas com disparo de mensagem direta no WhatsApp dos responsáveis.
* **Governança de Dados:** Rastreabilidade e conformidade jurídica com a LGPD no tratamento de dados de menores e registros de saúde.
* **Infraestrutura Escalável:** Arquitetura containerizada com separação de responsabilidades entre proxy, aplicação e storage.

---

## ⚡ Funcionalidades do Sistema

### 1. Diário de Classe e Frequência
* **Chamada Dinâmica:** Registro de presença ("Presente" ou "Falta") com um único clique.
* **Filtro por Turma:** Alternância instantânea entre as turmas **Manhã**, **Tarde** e **Noite**.
* **Navegação Histórica:** Seleção de datas anteriores via calendário interativo (Flatpickr) para consulta ou retificação de chamadas passadas.
* **Anexo de Atestados Médicos:** Envio de laudos e atestados (PDF ou imagem) que justificam automaticamente a falta do aluno no dia.

### 2. Cadastro e Prontuário do Aluno
* **Cadastro Completo:** Registro de dados pessoais, contatos de emergência e condições médicas.
* **Busca Automática de CEP:** Integração com a API do ViaCEP que autocompleta rua, bairro e cidade ao informar o CEP.
* **Máscara Inteligente de Telefone:** Formatação automática em tempo real no padrão nacional:
  * Celular: `(73) 98191-7878`
  * Fixo: `(73) 3281-1234`
* **Edição Cadastral:** Atualização completa de matrículas a qualquer momento pela coordenação.
* **Ficha A4 Pronta para Impressão:** Geração de ficha cadastral formatada em tamanho A4 com brasão institucional, dados médicos e termo de consentimento com campo para assinatura física.
* **Exclusão Segura (Direito ao Esquecimento):** Exclusão do aluno com limpeza automática em cascata de todas as suas presenças e remoção definitiva de seus arquivos de atestados.

### 3. Histórico Individual do Aluno
* Visão consolidada de todas as presenças, faltas e justificativas ao longo do ano letivo.
* Contadores e métricas de assiduidade do aluno com links diretos para visualização dos laudos anexados.

### 4. Dashboard da Coordenação e Alerta de Evasão
* Indicadores gerais de frequência diária da instituição.
* **Gatilho de Risco de Evasão:** Alunos com mais de 3 faltas são listados com um botão direto que abre o WhatsApp do responsável com mensagem personalizada já formatada:
  > *"Olá, notamos que o/a [Nome do Aluno] faltou [X] vezes na Casa de Lió. Está tudo bem com a família? Podemos ajudar?"*
* **Exportação CSV:** Download de relatório consolidado de frequências para auditorias e prestação de contas.

---

## 🏗 Arquitetura de Software

O sistema adota uma arquitetura em camadas orientada a microsserviços containerizados:

```mermaid
graph TD
    User["Navegador do Usuário / Dispositivo Móvel"]
    
    subgraph "Camada de Rede e Proxy"
        Nginx["Nginx Reverse Proxy (Porta 80)"]
    end
    
    subgraph "Camada de Aplicação"
        FastAPI["FastAPI + Uvicorn (Porta 8000)"]
        AuthMiddleware["Middleware de Sessão & Auth JWT"]
        StorageEngine["Motor de Armazenamento (storage.py)"]
    end
    
    subgraph "Camada de Dados e Arquivos"
        SQLite[("Banco de Dados SQLite (banco.db)")]
        MinIO["MinIO / AWS S3 Object Storage (Porta 9000/9001)"]
        LocalFS["Disco Local (pasta uploads/)"]
    end

    User -->|HTTP / HTTPS| Nginx
    Nginx -->|Proxy Pass (Max Upload 25MB)| FastAPI
    FastAPI --> AuthMiddleware
    FastAPI --> SQLite
    FastAPI --> StorageEngine
    StorageEngine -->|STORAGE_TYPE=s3 (Presigned URLs)| MinIO
    StorageEngine -->|STORAGE_TYPE=local| LocalFS
```

---

## 🛡 Conformidade com a LGPD

O sistema foi rigorosamente adaptado à **Lei Geral de Proteção de Dados Pessoais (Lei nº 13.709/2018)**:

| Exigência Legal da LGPD | Como o Sistema Atende |
| :--- | :--- |
| **Proteção de Menores (Art. 14)** | Consentimento específico e em destaque concedido pelos pais ou responsáveis legais gravado com timestamp no banco. |
| **Dados Sensíveis de Saúde (Art. 5º, II)** | Tratamento adequado para condições médicas e laudos, com visualização restrita exclusivamente a operadores autenticados. |
| **Prestação de Contas (Art. 37 & 46)** | Trilha de auditoria cronológica (`logs_auditoria`) registrando usuário, ação realizada, titular afetado e horário exato de cada operação crítica. |
| **Painel de Governança (`/auditoria`)** | Painel restrito à coordenação para emitir relatórios de conformidade e prestar esclarecimentos à ANPD. |
| **Direito de Eliminação (Art. 18, VI)** | Ao excluir um aluno, todos os registros e arquivos físicos anexados são eliminados definitivamente. |

---

## 🔐 Segurança e Autenticação

* **Criptografia de Senhas com `bcrypt`:** Salts criptográficos aleatórios por senha; as senhas nunca são armazenadas em texto puro.
* **Autenticação Stateless com JWT:** Emissão de tokens `pyjwt` criptografados com chave simétrica (`HS256`) e prazo de expiração configurável.
* **Cookies Protegidos (`HttpOnly` & `SameSite=Lax`):** Impede acesso aos tokens via scripts JavaScript maliciosos (mitigação contra ataques Cross-Site Scripting - XSS).
* **Migração Silenciosa de Contas Antigas:** Usuários legados têm suas senhas migradas para hash `bcrypt` de forma transparente no primeiro login bem-sucedido.
* **Controle de Acesso Baseado em Perfis (RBAC):**
  * `coordenacao`: Acesso irrestrito a cadastros, edições, exclusões, auditoria LGPD e criação de novos operadores.
  * `professor`: Acesso estrito à realização de chamadas e histórico escolar.

---

## 💻 Tecnologias Utilizadas

* **Linguagem Principal:** Python 3.12+
* **Framework Web:** FastAPI (ASGI de alta performance)
* **Servidor ASGI:** Uvicorn
* **Motor de Templates:** Jinja2
* **Segurança:** Bcrypt, PyJWT, ItsDangerous, Python-Dotenv
* **Cloud Storage SDK:** Boto3 (AWS S3 / MinIO)
* **Banco de Dados:** SQLite3 (com arquitetura compatível para migração PostgreSQL)
* **Frontend:** Bootstrap 5.3, Flatpickr (seleção de datas), Fontes e Paleta Institucional ADRA
* **Infraestrutura:** Docker, Docker Compose, Nginx (Alpine Linux)

---

## 📁 Estrutura do Projeto

```text
projeto-casa-de-lio/
├── app.py                  # Aplicação FastAPI principal e rotas
├── storage.py              # Módulo de abstração de armazenamento (Local / MinIO / S3)
├── setup_banco.py          # Script de inicialização do banco SQLite e usuários padrão
├── requirements.txt        # Dependências Python do ecossistema
├── .env                    # Variáveis de ambiente secretas (ignorado no Git)
├── .env.example            # Modelo de configuração de variáveis
├── .gitignore              # Regras de exclusão do repositório Git
├── Dockerfile              # Imagem Docker da aplicação
├── docker-compose.yml      # Orquestrador de serviços (Nginx, FastAPI, MinIO)
├── .dockerignore           # Arquivos ignorados no build Docker
├── banco.db                # Banco de dados local SQLite
├── nginx/
│   └── nginx.conf          # Configurações do servidor Nginx e proxy reverso
├── templates/              # Templates Jinja2 (HTML/CSS)
│   ├── base.html           # Layout mestre com menu, identidade visual e máscara de telefone
│   ├── index.html          # Tela principal de chamada diária e filtros
│   ├── login.html          # Tela de autenticação de usuários
│   ├── cadastro.html       # Formulário de matrícula com ViaCEP e Termo LGPD
│   ├── editar_aluno.html   # Edição cadastral completa
│   ├── lista_alunos.html   # Listagem de alunos com atalhos de gestão
│   ├── historico_aluno.html# Histórico anual consolidado de presença
│   ├── dashboard.html      # Métricas de frequência e alerta WhatsApp
│   ├── ficha.html          # Ficha A4 para impressão e assinatura física
│   ├── novo_usuario.html   # Cadastro de operadores do sistema
│   └── auditoria.html      # Painel de conformidade e governança LGPD
└── uploads/                # Diretório local para atestados médicos (quando STORAGE_TYPE=local)
```

---

## 🚀 Como Executar

### Opção 1: Execução Completa com Docker Compose

Esta é a opção recomendada para produção ou homologação. Ela provisiona a aplicação, o servidor Nginx e o MinIO automaticamente:

1. **Clonar o Repositório:**
   ```bash
   git clone <URL_DO_REPOSITORIO>
   cd projeto-casa-de-lio
   ```

2. **Configurar as Variáveis de Ambiente:**
   Copie o modelo de ambiente:
   ```bash
   cp .env.example .env
   ```
   *(Em ambiente Windows PowerShell: `Copy-Item .env.example .env`)*

3. **Subir os Containers:**
   ```bash
   docker compose up --build -d
   ```

4. **Acessar os Serviços:**
   * **Sistema Web (Nginx):** [http://localhost](http://localhost) (Porta 80)
   * **MinIO Console (Gerenciamento de Arquivos):** [http://localhost:9001](http://localhost:9001)
     * *Usuário padrão MinIO:* `minioadmin`
     * *Senha padrão MinIO:* `minioadmin`

---

### Opção 2: Execução Local Direta (Desenvolvimento)

Caso queira rodar diretamente na sua máquina sem Docker:

1. **Criar e Ativar Ambiente Virtual:**
   ```bash
   python -m venv venv
   # No Windows:
   .\venv\Scripts\activate
   # No Linux/Mac:
   source venv/bin/activate
   ```

2. **Instalar Dependências:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Garantir Arquivo `.env`:**
   Certifique-se de que o arquivo `.env` existe na raiz do projeto com as chaves configuradas:
   ```env
   SECRET_KEY=sua_chave_secreta_aqui
   ALGORITHM=HS256
   ACCESS_TOKEN_EXPIRE_MINUTES=480
   STORAGE_TYPE=local
   ```

4. **Inicializar o Banco de Dados:**
   ```bash
   python setup_banco.py
   ```

5. **Iniciar o Servidor:**
   ```bash
   uvicorn app:app --reload --port 8000
   ```

6. **Acessar a Aplicação:**
   Abra o navegador em [http://localhost:8000](http://localhost:8000).

---

## 🔑 Credenciais Padrão de Acesso

Após rodar `python setup_banco.py`:

| Perfil | Usuário | Senha Padrão | Nível de Acesso |
| :--- | :--- | :--- | :--- |
| **Coordenação** | `coord` | `123` | Acesso total (administrativo, LGPD, edições e usuários) |
| **Professor** | `prof` | `123` | Registro de presenças, anexos e histórico |

> [!IMPORTANT]
> No primeiro acesso em ambiente de produção, acerte as senhas para combinações fortes e remova contas de teste.

---

## ⚙️ Variáveis de Ambiente

As configurações sensíveis ficam no arquivo `.env`:

```env
# Segurança e Tokens JWT
SECRET_KEY=chave_criptografica_gerada_aleatoriamente
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480

# Ambiente
ENVIRONMENT=development
PORT=8000

# Provedor de Arquivos (local ou s3)
STORAGE_TYPE=local
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET_NAME=casa-de-lio-uploads
S3_REGION=us-east-1
S3_USE_SSL=false
```

---

## 🗺 Mapeamento de Rotas e Endpoints

| Método | Rota | Perfil Mínimo | Descrição |
| :--- | :--- | :--- | :--- |
| `GET` | `/login` | Público | Exibe tela de autenticação |
| `POST` | `/login` | Público | Valida credenciais e emite cookie JWT |
| `GET` | `/sair` | Autenticado | Efetua logout e destroi sessão |
| `GET` | `/` | Autenticado | Diário de chamada diária com filtros por turma |
| `GET` | `/registrar/{aluno_id}/{data}/{status}` | Autenticado | Registra "Presente" ou "Falta" para o aluno |
| `POST`| `/anexar_atestado/{aluno_id}/{data}` | Autenticado | Upload de atestado médico e justificativa automática |
| `GET` | `/uploads/{nome_arquivo}` | Autenticado | Visualização segura de atestados (Redirecionamento S3 ou Local) |
| `GET` | `/alunos/{aluno_id}/historico` | Autenticado | Histórico anual de assiduidade do aluno |
| `GET` | `/dashboard` | Coordenação | Painel com métricas e alertas de falta via WhatsApp |
| `GET` | `/relatorio/csv` | Coordenação | Exportação de planilha CSV com todas as frequências |
| `GET` | `/alunos` | Coordenação | Tabela de gestão e listagem geral de alunos |
| `GET` | `/cadastrar` | Coordenação | Tela de matrícula de novo aluno com Termo LGPD |
| `POST`| `/cadastrar` | Coordenação | Salva matrícula e grava consentimento LGPD |
| `GET` | `/alunos/editar/{id}` | Coordenação | Formulário de edição dos dados do aluno |
| `POST`| `/alunos/editar/{id}` | Coordenação | Atualiza dados e registra log de auditoria |
| `POST`| `/alunos/excluir/{id}` | Coordenação | Exclui aluno em cascata e remove atestados do storage |
| `GET` | `/ficha/{id}` | Coordenação | Folha A4 para impressão e assinatura física |
| `GET` | `/usuarios/novo` | Coordenação | Tela de criação de novos operadores |
| `POST`| `/usuarios/novo` | Coordenação | Cria usuário com senha criptografada em bcrypt |
| `GET` | `/auditoria` | Coordenação | Painel de trilha de auditoria e conformidade LGPD |

---

## 📚 Manuais Adicionais

Para aprofundamento, consulte a pasta [`docs/`](docs/):
* **[Manual de Arquitetura e Governança LGPD](docs/ARQUITETURA_E_LGPD.md):** Detalhamento dos fluxos de dados, políticas de segurança e conformidade com a Lei 13.709/2018.
* **[Manual do Usuário Operacional](docs/MANUAL_DO_USUARIO.md):** Guia passo a passo para professores e equipe de coordenação.

---

## 📄 Licença e Créditos

Desenvolvido para a **Casa de Lió / ADRA**. Todos os direitos reservados.
Projeto mantido com foco em impacto social, educação humanizada e segurança digital.
