class AppError(Exception):
    """Базовая ошибка приложения."""


class PdfError(AppError):
    """Ошибка работы с PDF."""


class PdfUnreadableError(PdfError):
    """PDF не открывается, повреждён или защищён паролем."""


class PdfNoTextError(PdfError):
    """В PDF не удалось найти ни текста, ни распознаваемых изображений."""


class UnsafeContentError(AppError):
    """Содержимое документа не прошло модерацию."""


class IngestCancelledError(AppError):
    """Обработка документа отменена (например, документ удалён)."""


class EmbeddingError(AppError):
    """Не удалось получить эмбеддинги."""


class LLMError(AppError):
    """Ошибка локальной языковой модели."""


class LLMTimeoutError(LLMError):
    """Модель не ответила за отведённое время."""


class LLMEmptyResponseError(LLMError):
    """Модель вернула пустой ответ."""
