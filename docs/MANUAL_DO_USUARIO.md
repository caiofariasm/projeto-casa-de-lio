# 📖 Manual do Usuário - Sistema Casa de Lió

Bem-vindo ao **Manual de Operação do Sistema de Gestão Escolar da Casa de Lió**. Este guia orienta professores e a equipe de coordenação no uso diário do sistema.

---

## 📌 Sumário
1. [Acessando o Sistema](#1-acessando-o-sistema)
2. [Realizando a Chamada Diária](#2-realizando-a-chamada-diária)
3. [Anexando Atestados e Justificando Faltas](#3-anexando-atestados-e-justificando-faltas)
4. [Cadastrando um Novo Aluno](#4-cadastrando-um-novo-aluno)
5. [Imprimindo a Ficha Cadastral A4](#5-imprimindo-a-ficha-cadastral-a4)
6. [Consultando o Histórico Individual de Presenças](#6-consultando-o-histórico-individual-de-presenças)
7. [Editando e Excluindo Matrículas](#7-editando-e-excluindo-matrículas)
8. [Usando o Dashboard e Alerta de Faltas no WhatsApp](#8-usando-o-dashboard-e-alerta-de-faltas-no-whatsapp)
9. [Consultando a Trilha de Auditoria LGPD](#9-consultando-a-trilha-de-auditoria-lgpd)
10. [Criando Novos Usuários Operadores](#10-criando-novos-usuários-operadores)

---

## 1. Acessando o Sistema

1. Abra o navegador de internet (Google Chrome, Firefox ou Edge) no computador, tablet ou celular.
2. Acesse o endereço do sistema: `http://localhost:8000` (ou o endereço configurado no servidor).
3. Na tela de login, informe seu **Usuário** e **Senha**:
   * **Perfil Professor:** Visualiza e executa a chamada diária, anexa atestados e consulta o histórico individual.
   * **Perfil Coordenação:** Possui acesso total, incluindo cadastro, edição, exclusão, relatórios CSV, usuários e auditoria LGPD.
4. Clique em **Entrar no Sistema**.

---

## 2. Realizando a Chamada Diária

A tela inicial (`/`) é o Diário de Classe Digital:

1. **Selecionar a Turma:**
   * No topo da lista, utilize o seletor de turmas para filtrar entre **Manhã**, **Tarde** ou **Noite**.
   * O sistema filtra a lista imediatamente mantendo o foco nos alunos daquela aula.
2. **Selecionar a Data:**
   * Por padrão, a data exibida é o dia de hoje.
   * Se precisar consultar ou lançar uma chamada passada, clique no campo de data e selecione o dia no calendário.
3. **Marcar Frequência:**
   * Clique em **✅ Presente** para registrar presença (botão verde).
   * Clique em **❌ Falta** para registrar falta (botão vermelho).
   * O sistema salva a resposta na hora sem recarregar a tela.

---

## 3. Anexando Atestados e Justificando Faltas

Quando um aluno apresentar um laudo ou atestado médico:

1. Localize a linha do aluno na tela de chamada da data correspondente à falta.
2. Na coluna **Atestado / Justificativa**, clique no botão **📎 Anexar Atestado**.
3. Selecione o arquivo no computador ou celular (aceita arquivos em **PDF, JPG, PNG**).
4. Confirme o envio.
5. **Resultado Automático:**
   * O sistema converte o status daquele dia para **"Falta justificada"** (badge azul).
   * Um botão azul **Visualizar Atestado** fica disponível para abrir o documento com segurança.

---

## 4. Cadastrando um Novo Aluno *(Exclusivo Coordenação)*

No menu superior, clique em **Cadastrar**:

1. **Bloco 1 - Identificação:**
   * Informe o Nome Completo, Data de Nascimento e Turma.
   * **Telefone Pessoal (WhatsApp):** Digite apenas os números; a máscara formatará automaticamente como `(73) 98191-7878`.
2. **Bloco 2 - Endereço Inteligente:**
   * Digite os 8 dígitos do **CEP**.
   * Ao sair do campo, o sistema busca na base dos Correios e preenche automaticamente **Rua, Bairro e Cidade**. Basta digitar o Número e o Complemento.
3. **Bloco 3 - Segurança e Saúde:**
   * Preencha o nome do Contato de Emergência e o **Telefone de Emergência** (também mascarado).
   * Descreva condições médicas relevantes (alergias a alimentos/medicamentos, uso de remédios contínuos, etc.).
4. **Bloco 4 - Menores de 18 Anos:**
   * Se o aluno for menor de idade, o sistema abre automaticamente os campos para o **Nome do Responsável Legal** e o termo de autorização.
5. **Bloco 5 - Termo de Consentimento e Privacidade (LGPD):**
   * Leia e marque a caixa obrigatória de ciência e autorização para o tratamento dos dados pessoais e de saúde.
6. Clique em **Salvar Matrícula**.

---

## 5. Imprimindo a Ficha Cadastral A4

Assim que o aluno é salvo (ou clicando no botão **📄 Ficha** na lista de alunos):

1. O sistema abre a folha oficial de matrícula, já desenhada com dimensões exatas de folha **A4**.
2. A folha contém os dados de identificação, endereço, contatos de socorro, observações médicas e o termo formal de responsabilidade LGPD.
3. Pressione `Ctrl + P` (ou clique no botão **Imprimir** do navegador).
4. Solicite a assinatura física do responsável no campo indicado e arquive na pasta física da turma.

---

## 6. Consultando o Histórico Individual de Presenças

Para acompanhar um aluno com dificuldades de assiduidade:

1. Na lista de chamada ou na tela de alunos, clique no botão **📋 Histórico**.
2. A tela exibe:
   * **Cards de Resumo:** Total de aulas registradas, total de presenças, faltas injustificadas e faltas justificadas com atestado.
   * **Tabela Cronológica:** Cada data registrada no ano com o status e o link para visualização do respectivo atestado médico.

---

## 7. Editando e Excluindo Matrículas *(Exclusivo Coordenação)*

Acesse o menu **Alunos** no topo:

* **Para Editar:** Clique no botão amarelo **✏️ Editar**. Atualize qualquer informação (turma, telefone, endereço) e clique em **Salvar Alterações**. O sistema grava a alteração na trilha de auditoria.
* **Para Excluir:** Clique no botão vermelho **🗑️ Excluir**.
  * O sistema solicitará confirmação para evitar cliques acidentais.
  * Ao confirmar, todas as presenças passadas e os laudos médicos anexados são permanentemente descartados do servidor, atendendo ao Direito de Eliminação da LGPD.

---

## 8. Usando o Dashboard e Alerta de Faltas no WhatsApp *(Exclusivo Coordenação)*

Acesse o menu **Dashboard**:

1. **Visão Geral:** Métricas com percentuais de presença geral da instituição.
2. **Exportar Relatório:** Clique no botão verde **Baixar Relatório (CSV)** para gerar uma planilha com todos os lançamentos para prestação de contas.
3. **Alerta Rápido no WhatsApp:**
   * A lista destaca em vermelho os alunos em risco com **3 ou mais faltas acumuladas**.
   * Ao lado de cada aluno, clique no botão verde **💬 Avisar Responsável no WhatsApp**.
   * O sistema abre o WhatsApp Web (ou o aplicativo no celular) com o número do responsável formatado e com o texto pronto:
     > *"Olá, notamos que o/a [Nome] faltou [X] vezes na Casa de Lió. Está tudo bem com a família? Podemos ajudar?"*

---

## 9. Consultando a Trilha de Auditoria LGPD *(Exclusivo Coordenação)*

Acesse o menu **Auditoria LGPD**:

1. Uma tabela exibe as últimas 100 operações de segurança realizadas no sistema.
2. É possível fiscalizar:
   * Data e hora exatas de cada ação.
   * Qual operador realizou a ação (ex: `coord`).
   * Qual aluno ou usuário foi impactado.
3. Utilize essa tela para fiscalizações internas e conformidade com a ANPD.

---

## 10. Criando Novos Usuários Operadores *(Exclusivo Coordenação)*

Para dar acesso a um novo professor ou membro da equipe:

1. No menu superior direito, clique em **Opções ▾** e selecione **⚙️ Criar Novo Acesso**.
2. Escolha um nome de usuário (login) e uma senha segura.
3. Selecione o perfil de acesso:
   * **Professor:** Acesso restrito a chamadas e históricos.
   * **Coordenação:** Acesso total à administração.
4. Clique em **Cadastrar Usuário**. A senha é criptografada automaticamente em hash `bcrypt`.
