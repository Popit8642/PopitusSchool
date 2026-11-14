import logging
import time
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from arq import func

from bot_factory import create_bot
from config import settings
from database.database import engine
from database.functions import (
    get_document_with_user,
    get_documents_for_recovery,
    mark_document_failed,
    set_document_status,
)
from database.models import DOC_COMPLETED, DOC_FAILED, DOC_PENDING
from errors import IngestCancelledError, PdfError
from logging_setup import setup_logging
from services.ingest import ingest_document
from services.queue import (
    close_pool,
    enqueue_ingest,
    get_queued_document_ids,
    redis_settings,
)
from services.rate_limit import close_redis


logger = logging.getLogger(__name__)


async def _safe_edit(
    bot: Bot, chat_id: int | None, message_id: int | None, text: str
) -> bool:
    if not chat_id or not message_id:
        return False
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text
        )
        return True
    except TelegramAPIError as exc:
        logger.warning("Не удалось изменить сообщение %s: %s", message_id, exc)
    except Exception:
        logger.exception("Ошибка сети при изменении сообщения %s", message_id)
    return False


async def _notify(document: dict, text: str, bot: Bot) -> None:
    """Пытается изменить исходное сообщение, иначе отправляет новое."""
    if await _safe_edit(
        bot,
        document.get("notify_chat_id"),
        document.get("notify_message_id"),
        text,
    ):
        return

    chat_id = document.get("notify_chat_id") or document.get("telegram_id")
    if not chat_id:
        return
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except TelegramAPIError as exc:
        logger.warning("Не удалось уведомить %s: %s", chat_id, exc)
    except Exception:
        logger.exception("Ошибка сети при уведомлении %s", chat_id)


def _delete_source(document: dict) -> None:
    source_file = document.get("source_file")
    if not source_file:
        return
    path = settings.upload_dir / Path(source_file).name
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Не удалось удалить файл %s", path)


async def process_document_task(ctx, document_id: str):
    started = time.monotonic()
    bot: Bot = ctx["bot"]

    document = await get_document_with_user(document_id)
    if document is None:
        logger.warning("Задача для документа %s: документ не найден", document_id)
        return

    if document["status"] == DOC_COMPLETED:
        logger.info("Документ %s уже обработан, пропускаю", document_id)
        return

    await _notify(
        document, f"⏳ Начинаю обрабатывать «{document['title']}»...", bot
    )

    try:
        stats = await ingest_document(document["id"])
    except PdfError:
        logger.exception("Ошибка распознавания PDF %s", document_id)
        await _notify(
            document,
            "❌ Не удалось распознать текст в PDF. Проверьте, что файл не "
            "повреждён и содержит текст или качественные сканы.",
            bot,
        )
    except IngestCancelledError:
        logger.info("Обработка документа %s отменена", document_id)
        return
    except Exception:
        logger.exception("Ошибка обработки документа %s", document_id)
        await _notify(
            document,
            "❌ Произошла ошибка при обработке учебника, попробуйте позже.",
            bot,
        )
    else:
        await _notify(
            document,
            f"✅ Учебник «{document['title']}» добавлен. "
            f"Страниц: {stats['pages']}, фрагментов: {stats['chunks']}.",
            bot,
        )
    finally:
        try:
            refreshed = await get_document_with_user(document["id"])
            if refreshed and refreshed["status"] in (DOC_COMPLETED, DOC_FAILED):
                _delete_source(refreshed)
        except Exception:
            logger.exception("Не удалось удалить исходный файл %s", document_id)
        logger.info(
            "Задача %s завершена за %.1f сек",
            document_id,
            time.monotonic() - started,
        )


async def _recover_documents(bot: Bot) -> None:
    documents = await get_documents_for_recovery()
    if not documents:
        return

    logger.info("Восстановление задач: найдено %s документов", len(documents))
    queued_ids = await get_queued_document_ids()
    for document in documents:
        source_file = document.get("source_file")
        path = settings.upload_dir / Path(source_file).name if source_file else None
        if not path or not path.exists() or path.stat().st_size == 0:
            await mark_document_failed(
                document["id"], "Исходный PDF-файл не найден"
            )
            await _notify(
                document,
                "❌ Не удалось возобновить обработку: исходный файл потерян. "
                "Загрузите PDF заново.",
                bot,
            )
            continue

        document_id = str(document["id"])
        if (
            document.get("status") == DOC_PENDING
            and document_id in queued_ids
        ):
            logger.info(
                "Документ %s уже ждёт в очереди, восстановление не нужно",
                document_id,
            )
            continue

        await set_document_status(document["id"], DOC_PENDING)
        try:
            job_id = await enqueue_ingest(document["id"])
            logger.info(
                "Документ %s возвращён в очередь: %s", document["id"], job_id
            )
            await _notify(
                document,
                f"🔄 Продолжаю обработку «{document['title']}» после перезапуска...",
                bot,
            )
        except Exception:
            logger.exception(
                "Не удалось вернуть документ %s в очередь", document["id"]
            )


async def _cleanup_orphan_uploads() -> None:
    """Удаляет старые файлы, не связанные с активными документами."""
    active = await get_documents_for_recovery()
    keep = {
        Path(item["source_file"]).name
        for item in active
        if item.get("source_file")
    }
    now = time.time()
    for path in settings.upload_dir.glob("*.pdf"):
        if path.name in keep:
            continue
        try:
            if now - path.stat().st_mtime > 3600:
                path.unlink(missing_ok=True)
                logger.info("Удалён осиротевший файл %s", path.name)
        except OSError:
            logger.exception("Не удалось удалить осиротевший файл %s", path)


async def startup(ctx):
    setup_logging()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    ctx["bot"] = create_bot()
    logger.info("Воркер запущен")
    try:
        await _recover_documents(ctx["bot"])
        await _cleanup_orphan_uploads()
    except Exception:
        logger.exception("Ошибка восстановления задач при старте воркера")


async def shutdown(ctx):
    try:
        await ctx["bot"].session.close()
    except Exception:
        logger.exception("Ошибка при закрытии сессии бота")
    await close_pool()
    await close_redis()
    await engine.dispose()
    logger.info("Воркер остановлен")


class WorkerSettings:
    functions = [
        func(
            process_document_task,
            max_tries=2,
            timeout=settings.INGEST_JOB_TIMEOUT_SECONDS,
        )
    ]
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1
