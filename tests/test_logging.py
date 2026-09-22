import logging

from support_doc_extractor.logging_utils import configure_logging, get_logger


def test_configure_logging_is_idempotent():
    logger = configure_logging("INFO", force=True)
    first_handlers = list(logger.handlers)

    logger_again = configure_logging("DEBUG")

    assert logger_again is logger
    assert list(logger.handlers) == first_handlers


def test_get_logger_uses_package_namespace():
    configure_logging("INFO", force=True)
    logger = get_logger("engine")

    assert logger.name == "support_doc_extractor.engine"
    assert logger.isEnabledFor(logging.INFO)
