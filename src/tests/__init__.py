import logging

from mullvad import logger


# Overwriting the create_logger function in logger to instead return a dummy
# logger that simply ignores all log records that are sent to it. This also
# removes the requirement to initialize the logger module.
def create_dummy_logger(*args):
    logger = logging.getLogger()
    logger.addHandler(logging.NullHandler())
    return logger
logger.create_logger = create_dummy_logger
