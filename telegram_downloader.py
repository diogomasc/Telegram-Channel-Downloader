#!/usr/bin/env python3
"""
Telegram Channel/Group Downloader
Downloads all media and text from a Telegram channel in chronological order.
Parses glossary/menu messages to organize content by lesson name.
"""

# pylint: disable=missing-function-docstring,logging-fstring-interpolation,broad-exception-caught,too-many-arguments,too-many-positional-arguments,too-many-locals

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

import aiofiles
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from rich.prompt import Confirm, Prompt
from rich.table import Table
from telethon import TelegramClient, utils  # type: ignore[import-untyped]
from telethon.errors import FloodWaitError  # type: ignore[import-untyped]
from telethon.tl.types import (  # type: ignore[import-untyped]
    DocumentAttributeFilename,
    MessageMediaDocument,
    MessageMediaPhoto,
)

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, markup=True)],
)
log = logging.getLogger("tgdl")
console = Console()

# ─── Constants ────────────────────────────────────────────────────────────────
CONFIG_FILE = Path(".tgdl_config.json")
STATE_FILE = Path(".tgdl_state.json")
DEFAULT_WORKERS = 4  # concurrent downloads
CHUNK_SIZE = 512 * 1024  # 512 KB per chunk — Telethon default uses 64 KB
MAX_RETRIES = 5
RETRY_DELAY = 5  # seconds
RETURN_MENU_PROMPT = "\nPressione [bold]Enter[/bold] para voltar ao menu"


# ─── Config ───────────────────────────────────────────────────────────────────


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    CONFIG_FILE.chmod(0o600)
    log.info(f"Credenciais salvas em [bold]{CONFIG_FILE}[/bold]")


def prompt_credentials(cfg: dict) -> dict:
    console.print(
        Panel.fit(
            "[bold cyan]Configuração inicial do Telegram Downloader[/bold cyan]\n"
            "Obtenha API_ID e API_HASH em "
            "[link=https://my.telegram.org]https://my.telegram.org[/link]",
            border_style="cyan",
        )
    )
    if "api_id" not in cfg:
        cfg["api_id"] = int(Prompt.ask("[bold]API_ID[/bold]"))
    if "api_hash" not in cfg:
        cfg["api_hash"] = Prompt.ask("[bold]API_HASH[/bold]", password=True)
    if "phone" not in cfg:
        cfg["phone"] = Prompt.ask("[bold]Telefone[/bold] (ex: +5511999999999)")
    save_config(cfg)
    return cfg


# ─── State (resume support) ───────────────────────────────────────────────────


def load_state(channel: Union[str, int]) -> dict:
    channel_str = str(channel)
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            all_states = json.load(f)
        return all_states.get(channel_str, {})
    return {}


def save_state(channel: Union[str, int], state: dict) -> None:
    channel_str = str(channel)
    all_states: dict = {}
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            all_states = json.load(f)
    all_states[channel_str] = state
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_states, f, indent=2)


# ─── Glossary Parser ──────────────────────────────────────────────────────────

TAG_RE = re.compile(r"#([FA]\d+)", re.IGNORECASE)
LESSON_RE = re.compile(
    r"[=\-–]?\s*(\d+[\.\d]*)\s*[-–]?\s*(.+?)\s*(?=#[FA]\d+|$)", re.IGNORECASE
)


def process_glossary_entry(entry: str, mapping: Dict[str, str]) -> None:
    tags = TAG_RE.findall(entry)
    lesson_match = LESSON_RE.search(entry)
    if tags and lesson_match:
        num = lesson_match.group(1).strip()
        title = lesson_match.group(2).strip()
        title = TAG_RE.sub("", title).strip(" -–")
        label = f"{num} - {title}" if num not in title else title
        for tag in tags:
            mapping[tag.upper()] = label


def parse_glossary(text: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    entries = re.split(r"(?=\s*=\s*\d+[\.\d]*)", text)
    for entry in entries:
        process_glossary_entry(entry, mapping)
    return mapping


def is_glossary_message(text: str) -> bool:
    return len(TAG_RE.findall(text)) >= 3


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(". ")
    return name[:200]


# ─── Download helpers ─────────────────────────────────────────────────────────


def get_document_filename(doc) -> Optional[str]:
    for attr in doc.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    mime = doc.mime_type or "application/octet-stream"
    ext = mime.split("/")[-1].split(";")[0]
    return f"file.{ext}"


def resolve_document_name(doc, msg_id: int) -> str:
    fname = get_document_filename(doc)
    if fname and fname.startswith("file."):
        return f"file_{msg_id}.{fname.split('.')[-1]}"
    return fname or f"file_{msg_id}"


def get_media_filename(message) -> Optional[str]:
    if not message.media:
        return None
    if isinstance(message.media, MessageMediaPhoto):
        return f"photo_{message.id}.jpg"
    if isinstance(message.media, MessageMediaDocument):
        return resolve_document_name(message.media.document, message.id)
    return None


def find_lesson_label(
    text: str, original_name: str, glossary: Dict[str, str]
) -> Optional[str]:
    tags = TAG_RE.findall(text)
    for tag in tags:
        if tag.upper() in glossary:
            return glossary[tag.upper()]
    m = TAG_RE.search(original_name)
    if m and m.group(1).upper() in glossary:
        return glossary[m.group(1).upper()]
    return None


def get_message_text(message) -> str:
    return message.text or message.message or ""


def build_final_filename(seq: int, original_name: str, msg_id: int) -> str:
    padded = str(seq).zfill(5)
    if original_name:
        return f"{padded}_{sanitize_filename(original_name)}"
    return f"{padded}_msg_{msg_id}"


def build_output_path(
    base_dir: Path, message, glossary: Dict[str, str], seq: int
) -> Tuple[Path, str]:
    text = get_message_text(message)
    original_name = get_media_filename(message) or ""
    lesson_label = find_lesson_label(text, original_name, glossary)

    if lesson_label:
        folder = base_dir / sanitize_filename(lesson_label)
    else:
        folder = base_dir / "misc"

    folder.mkdir(parents=True, exist_ok=True)
    filename = build_final_filename(seq, original_name, message.id)
    return folder, filename


def get_media_size(media) -> int:
    if isinstance(media, MessageMediaDocument):
        return media.document.size
    return 0


def create_progress_callback(progress, task_id):
    state = {"downloaded": 0}

    def callback(current, total_bytes):
        delta = current - state["downloaded"]
        state["downloaded"] = current
        progress.update(task_id, advance=delta)
        if total_bytes:
            progress.update(task_id, total=total_bytes)

    return callback


async def download_attempt(client, message, dest, progress, task_id):
    cb = create_progress_callback(progress, task_id)
    await client.download_media(message, file=str(dest), progress_callback=cb)
    progress.update(task_id, completed=progress.tasks[task_id].total or 1)


async def handle_download_exception(exc, attempt):
    if attempt < MAX_RETRIES:
        log.warning(
            f"Erro (tentativa {attempt}/{MAX_RETRIES}): {exc} — retentando em {RETRY_DELAY}s"
        )
        await asyncio.sleep(RETRY_DELAY * attempt)
        return True
    log.error(f"Falha após {MAX_RETRIES} tentativas: {exc}")
    return False


async def execute_download_attempt(
    client, message, dest, progress, task_id, attempt
) -> Tuple[bool, bool]:
    try:
        await download_attempt(client, message, dest, progress, task_id)
        return True, False
    except FloodWaitError as e:
        log.warning(f"FloodWait: aguardando {e.seconds}s...")
        await asyncio.sleep(e.seconds + 1)
        return False, True
    except Exception as exc:
        should_retry = await handle_download_exception(exc, attempt)
        return False, should_retry


async def download_media_with_retry(
    client, message, dest, progress, task_id, semaphore
) -> bool:
    async with semaphore:
        total = get_media_size(message.media)
        if total:
            progress.update(task_id, total=total)

        for attempt in range(1, MAX_RETRIES + 1):
            success, retry = await execute_download_attempt(
                client, message, dest, progress, task_id, attempt
            )
            if success:
                return True
            if not retry:
                return False
    return False


# ─── Main download loop ───────────────────────────────────────────────────────


async def extract_glossary_from_messages(client, entity, glossary) -> None:
    async for msg in client.iter_messages(entity, reverse=True):
        txt = get_message_text(msg)
        if txt and is_glossary_message(txt):
            parsed = parse_glossary(txt)
            if parsed:
                glossary.update(parsed)
                log.info(
                    f"Glossário: {len(parsed)} entradas encontradas na msg {msg.id}"
                )


def save_and_display_glossary(glossary, state, channel) -> None:
    if glossary:
        console.print(
            f"[green]✓[/green] Glossário: [bold]{len(glossary)}[/bold] entradas"
        )
        tbl = Table("Tag", "Título da aula", show_header=True, header_style="bold cyan")
        for k, v in sorted(glossary.items())[:20]:
            tbl.add_row(f"#{k}", v)
        console.print(tbl)
        state["glossary"] = glossary
        save_state(channel, state)
    else:
        console.print(
            "[yellow]Nenhum glossário detectado — arquivos irão para 'misc'[/yellow]"
        )


async def build_glossary_pass(client, entity, state, channel) -> Dict[str, str]:
    glossary = state.get("glossary", {})
    if glossary:
        return glossary

    console.print("[dim]Construindo glossário de aulas...[/dim]")
    await extract_glossary_from_messages(client, entity, glossary)
    save_and_display_glossary(glossary, state, channel)
    return glossary


def create_progress_bar() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        TimeElapsedColumn(),
        MofNCompleteColumn(),
        console=console,
        expand=True,
    )


def update_state_after_download(channel, state, msg_id, downloaded_ids, seq):
    downloaded_ids.add(msg_id)
    state.update(
        {
            "last_message_id": msg_id,
            "downloaded_ids": list(downloaded_ids),
            "seq": seq,
        }
    )
    save_state(channel, state)


async def save_text_file(text: str, out_dir: Path, fname: str) -> None:
    if text:
        txt_path = out_dir / f"{fname}.txt"
        async with aiofiles.open(txt_path, "w", encoding="utf-8") as f:
            await f.write(text)


async def throttle_tasks(tasks, workers):
    if len(tasks) >= workers * 2:
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        return list(pending)
    return tasks


async def handle_media_download(
    client,
    message,
    base_dir,
    glossary,
    seq,
    state,
    channel,
    downloaded_ids,
    progress,
    semaphore,
    tasks,
    workers,
):
    out_dir, fname = build_output_path(base_dir, message, glossary, seq)
    dest = out_dir / fname

    await save_text_file(get_message_text(message), out_dir, fname)

    if dest.exists():
        update_state_after_download(channel, state, message.id, downloaded_ids, seq)
        return tasks

    task_id = progress.add_task(f"[white]{fname[:50]}", total=None, start=True)
    coro = download_media_with_retry(
        client, message, dest, progress, task_id, semaphore
    )

    async def _wrapped():
        ok = await coro
        if ok:
            update_state_after_download(channel, state, message.id, downloaded_ids, seq)
        else:
            log.error(f"Falhou: {dest}")
        progress.remove_task(task_id)

    tasks.append(asyncio.ensure_future(_wrapped()))
    return await throttle_tasks(tasks, workers)


async def handle_text_message(message, base_dir, seq, state, channel, downloaded_ids):
    text = get_message_text(message)
    out_dir = base_dir / "textos"
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / f"{str(seq).zfill(5)}_msg_{message.id}.txt"
    async with aiofiles.open(txt_path, "w", encoding="utf-8") as f:
        await f.write(f"=== Mensagem {message.id} | {message.date} ===\n\n{text}\n")
    update_state_after_download(channel, state, message.id, downloaded_ids, seq)


async def dispatch_message(
    client,
    message,
    base_dir,
    glossary,
    seq,
    state,
    channel,
    downloaded_ids,
    progress,
    semaphore,
    tasks,
    workers,
):
    if message.media:
        return await handle_media_download(
            client,
            message,
            base_dir,
            glossary,
            seq,
            state,
            channel,
            downloaded_ids,
            progress,
            semaphore,
            tasks,
            workers,
        )
    if message.text or message.message:
        await handle_text_message(
            message, base_dir, seq, state, channel, downloaded_ids
        )
    return tasks


async def process_messages_pass(
    client, entity, base_dir, glossary, state, channel, workers
):
    downloaded_ids = set(state.get("downloaded_ids", []))
    seq = state.get("seq", 0)
    semaphore = asyncio.Semaphore(workers)
    tasks = []

    total_msgs = (await client.get_messages(entity, limit=0)).total
    progress = create_progress_bar()

    with progress:
        overall = progress.add_task("[green]Progresso geral", total=total_msgs)
        async for message in client.iter_messages(entity, reverse=True):
            progress.advance(overall)
            if message.id in downloaded_ids:
                seq += 1
                continue

            seq += 1
            tasks = await dispatch_message(
                client,
                message,
                base_dir,
                glossary,
                seq,
                state,
                channel,
                downloaded_ids,
                progress,
                semaphore,
                tasks,
                workers,
            )

        if tasks:
            await asyncio.gather(*tasks)


async def run(
    client: TelegramClient,
    channel: Union[str, int],
    base_dir: Path,
    workers: int,
    resume: bool,
) -> None:
    console.print(f"\n[bold green]Conectando ao canal:[/bold green] {channel}")
    entity = await client.get_entity(channel)
    title = getattr(entity, "title", channel)
    console.print(f"[cyan]Canal:[/cyan] {title}")

    state = load_state(channel) if resume else {}
    glossary = await build_glossary_pass(client, entity, state, channel)

    await process_messages_pass(
        client, entity, base_dir, glossary, state, channel, workers
    )

    console.print(
        Panel.fit(
            f"[bold green]✓ Download concluído![/bold green]\n"
            f"Arquivos salvos em: [bold]{base_dir.resolve()}[/bold]",
            border_style="green",
        )
    )


# ─── Entry point ──────────────────────────────────────────────────────────────


def show_menu() -> str:
    console.clear()
    console.print(
        Panel.fit(
            "[bold magenta]📥 Telegram Channel Downloader[/bold magenta]\n"
            "[dim]Menu Principal[/dim]",
            border_style="magenta",
        )
    )
    console.print("1. Listar Canais e Grupos")
    console.print("2. Baixar Conteúdo")
    console.print("3. Sair\n")
    return Prompt.ask(
        "[bold cyan]Escolha uma opção[/bold cyan]", choices=["1", "2", "3"]
    )


async def get_dialogs(client):
    dialogs = []
    async for dialog in client.iter_dialogs():
        if dialog.is_channel or dialog.is_group:
            dialogs.append((dialog.name or "Sem Nome", dialog.id))
    return dialogs


def print_dialogs(dialogs):
    dialogs.sort(key=lambda x: x[0].lower())
    tbl = Table("Nome do Canal/Grupo", "ID", show_header=True, header_style="bold cyan")
    for name, did in dialogs:
        tbl.add_row(name, str(did))
    console.print(tbl)


async def handle_list_channels(client):
    console.clear()
    console.print("[dim]Buscando canais e grupos...[/dim]")
    try:
        dialogs = await get_dialogs(client)
        print_dialogs(dialogs)
    except Exception as e:
        log.error(f"Erro ao listar canais: {e}")
    Prompt.ask(RETURN_MENU_PROMPT, default="")


async def prompt_download_params(client, channel_input):
    channel_to_fetch = (
        int(channel_input) if channel_input.lstrip("-").isdigit() else channel_input
    )
    console.print("[dim]Obtendo informações do canal...[/dim]")
    entity = await client.get_entity(channel_to_fetch)
    channel_name = utils.get_display_name(entity) or str(channel_to_fetch)

    default_dir = os.path.join("download", sanitize_filename(channel_name))
    out_dir = Prompt.ask("[bold]Pasta de saída[/bold]", default=default_dir)
    workers = int(
        Prompt.ask("[bold]Downloads simultâneos[/bold]", default=str(DEFAULT_WORKERS))
    )
    resume = Confirm.ask("[bold]Retomar download anterior?[/bold]", default=True)
    return entity, channel_to_fetch, Path(out_dir), workers, resume


async def handle_download_content(client):
    console.clear()
    console.print(
        Panel.fit("[bold cyan]Baixar Conteúdo[/bold cyan]", border_style="cyan")
    )

    channel_input = Prompt.ask(
        "[bold]ID do Canal ou grupo[/bold] (username, link público ou ID numérico)"
    )
    if not channel_input.strip():
        console.print("[red]ID inválido.[/red]")
        Prompt.ask(RETURN_MENU_PROMPT, default="")
        return

    try:
        (
            _,
            channel_to_fetch,
            base_dir,
            workers,
            resume,
        ) = await prompt_download_params(client, channel_input)
        base_dir.mkdir(parents=True, exist_ok=True)
        await run(client, channel_to_fetch, base_dir, workers, resume)
    except ValueError as ve:
        log.error(f"Canal não encontrado ou inválido: {ve}")
    except Exception as e:
        log.error(f"Erro durante o download: {e}")

    Prompt.ask(RETURN_MENU_PROMPT, default="")


async def interactive_menu(client: TelegramClient) -> None:
    while True:
        choice = show_menu()
        if choice == "1":
            await handle_list_channels(client)
        elif choice == "2":
            await handle_download_content(client)
        elif choice == "3":
            console.print("[dim]Saindo...[/dim]")
            break


async def main() -> None:
    cfg = load_config()
    cfg = prompt_credentials(cfg)
    session_file = (
        CONFIG_FILE.parent / f".tgdl_{cfg['phone'].replace('+', '').replace(' ', '')}"
    )

    client = TelegramClient(
        str(session_file),
        cfg["api_id"],
        cfg["api_hash"],
        connection_retries=10,
        retry_delay=1,
        request_retries=10,
    )

    console.print("[dim]Iniciando cliente do Telegram...[/dim]")
    await client.start(phone=cfg["phone"])

    try:
        await interactive_menu(client)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrompido pelo usuário. Progresso salvo.[/yellow]")
        sys.exit(0)
