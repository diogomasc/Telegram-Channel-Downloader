# Telegram Channel Downloader

Baixa **todo o conteúdo** de um canal ou grupo do Telegram em ordem cronológica (do mais antigo ao mais recente), com suporte a glossário de aulas, retomada de download e uso máximo de banda.

---

## Funcionalidades

| Recurso | Detalhe |
|---|---|
| Ordem cronológica | Mais antigo → mais recente |
| Tipos de mídia | Vídeos, documentos, imagens, áudios, qualquer arquivo |
| Texto junto à mídia | Salva `.txt` ao lado de cada arquivo |
| Glossário automático | Detecta mensagens de menu (#F01, #A01) e organiza em pastas por aula |
| Downloads paralelos | N downloads simultâneos configurável |
| Retomada | Interrompa e continue de onde parou |
| Credenciais seguras | API_ID/HASH salvas localmente, nunca no git |

---

## Pré-requisitos

1. Conta Telegram
2. Obter **API_ID** e **API_HASH** em [https://my.telegram.org](https://my.telegram.org) → *API development tools*

---

## Instalação local (Python 3.9+)

```bash
# 1. Clone o repositório
git clone https://github.com/diogomasc/Telegram-Channel-Downloader.git
cd Telegram-Channel-Downloader

# 2. Crie ambiente virtual (recomendado)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Instale dependências
pip install -r requirements.txt

# 4. Execute
python telegram_downloader.py
```

Na **primeira execução** o script pergunta:

```
API_ID: 12345678
API_HASH: ********************************
Telefone: +5511999999999
```

As credenciais são salvas em `.tgdl_config.json` (modo 600, excluído do git). Nas próximas execuções não são pedidas novamente.

---

## 🐳 Docker

### Build

```bash
docker build -t tgdl .
```

### Execução

```bash
docker run -it --rm \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/download:/app/download \
  tgdl
```

- `/app/config` — onde ficarão a sessão (`.session`) e o `.tgdl_config.json`
- `/app/download` — onde os arquivos serão salvos

---

## 📂 Estrutura de saída

```
download/canal_nome/
├── 1.1 - Como vamos aprender Python/
│   ├── 00001_video_introducao.mp4
│   └── 00001_video_introducao.mp4.txt   ← texto/legenda da mensagem
├── 1.2 - Criando nosso primeiro script/
│   ├── 00003_1.2 - Criando nosso primeiro script.mp4
│   └── 00003_1.2 - Criando nosso primeiro script.mp4.txt
├── misc/                                ← mídia sem tag de aula
└── textos/                              ← mensagens somente-texto
```

---

## ⚙️ Menu Interativo

Ao rodar o script, você terá um menu interativo com as seguintes opções:

1. **Listar Canais e Grupos**: Busca na sua conta todos os canais e grupos disponíveis. Mostra o **Nome (em ordem alfabética)** e o **ID numérico** de cada um. Ideal para descobrir qual ID usar para o download.
2. **Baixar Conteúdo**: Pede os seguintes parâmetros para iniciar o download:
   - **ID do Canal ou grupo**: Username (`@canal`), link público ou ID numérico (encontrado na opção 1).
   - **Pasta de saída**: Diretório local de destino (Padrão: `download/Nome_do_Canal`).
   - **Downloads simultâneos**: Paralelismo (mais = mais rápido, respeite rate-limits. Padrão: `4`).
   - **Retomar download?**: Continua de onde parou (Padrão: `Sim`).
3. **Sair**: Encerra o script.

A cada retorno ao menu principal, a tela é limpa para facilitar a navegação.

---

## 🗂️ Glossário / Menu

O script detecta automaticamente mensagens que funcionam como índice do canal, como:

```
=1.1 - Como vamos aprender Python #F01 #F02
=1.2 - Criando nosso primeiro script #F03
```

E usa as tags `#F01`, `#A01`, etc., para organizar cada arquivo na pasta da aula correspondente.

---

## 🔒 Segurança

- `.tgdl_config.json` e `.tgdl_*.session` são criados com permissões `600`
- O `.gitignore` incluído garante que nunca sejam commitados
- No Docker, o container roda como usuário não-root (`tgdl`, uid 1000)

---

## 📦 Dependências principais

| Pacote | Função |
|---|---|
| `telethon` | Cliente MTProto do Telegram |
| `aiofiles` | I/O assíncrono de arquivos |
| `rich` | Interface de progresso no terminal |
| `cryptg` | Aceleração nativa de criptografia (velocidade) |

---

## ⚠️ Avisos

- Use apenas para canais nos quais você tenha **permissão** para baixar o conteúdo.
- O Telegram impõe rate-limits; o script os respeita automaticamente (`FloodWaitError`).
- Para canais muito grandes (10 000+ mensagens) prefira rodar com Docker para isolar dependências.

---

## 📄 Licença

MIT
